import asyncio
from collections.abc import Mapping
from contextlib import suppress
from typing import Any, Protocol

from fastapi import WebSocket
from pydantic import TypeAdapter

from pyrpckit.app import RpcChannel
from pyrpckit.codec import RpcCodec
from pyrpckit.dependencies import EmptyResolver, RpcResolver, connection_scope
from pyrpckit.envelopes import RpcNotification
from pyrpckit.protocol import RpcNotificationDefinition
from pyrpckit.server import RpcErrorMapper

__all__ = ["serve"]


class WebSocketConnection(Protocol):
    async def accept(self, subprotocol: str | None = None) -> None: ...

    async def receive(self) -> Mapping[str, Any]: ...

    async def send_text(self, data: str) -> None: ...


async def serve(
    channel: RpcChannel,
    websocket: WebSocketConnection,
    *,
    context: object | Mapping[type[Any], object] | None = None,
    resolver: RpcResolver | None = None,
    error_mapper: RpcErrorMapper | None = None,
    max_concurrency: int = 32,
    max_queue_size: int = 128,
    subprotocol: str | None = None,
) -> None:
    """Accept and serve one socket until disconnect.

    Call from a normal FastAPI WebSocket endpoint after its dependencies resolve.
    Context values and the WebSocket are injectable into RPC methods and events.
    The runtime owns acceptance and RPC task cleanup; FastAPI owns dependency cleanup.
    """
    if max_concurrency < 1:
        raise ValueError("max_concurrency must be at least 1")
    if max_queue_size < 1:
        raise ValueError("max_queue_size must be at least 1")
    values: dict[type[Any], object] = {WebSocket: websocket}
    if isinstance(context, Mapping):
        values.update(context)
    elif context is not None:
        values[type(context)] = context
    runtime = _RpcWebSocketRuntime(
        channel,
        resolver=resolver if resolver is not None else EmptyResolver(),
        error_mapper=error_mapper,
        max_concurrency=max_concurrency,
        max_queue_size=max_queue_size,
        subprotocol=subprotocol,
    )
    await runtime.serve(websocket, context=values)


class _RpcWebSocketRuntime:
    def __init__(
        self,
        channel: RpcChannel,
        *,
        resolver: RpcResolver,
        error_mapper: RpcErrorMapper | None,
        max_concurrency: int,
        max_queue_size: int,
        subprotocol: str | None,
    ) -> None:
        self._channel = channel
        self._resolver = resolver
        self._error_mapper = error_mapper
        self._max_concurrency = max_concurrency
        self._max_queue_size = max_queue_size
        self._subprotocol = subprotocol
        self._codec = RpcCodec()

    async def serve(
        self,
        websocket: WebSocketConnection,
        *,
        context: object | Mapping[type[Any], object] | None = None,
    ) -> None:
        async with connection_scope(self._resolver, context) as resolver:
            server = self._channel.server(
                resolver=resolver,
                error_mapper=self._error_mapper,
            )
            await websocket.accept(subprotocol=self._subprotocol)
            outgoing: asyncio.Queue[str] = asyncio.Queue(self._max_queue_size)
            concurrency = asyncio.Semaphore(self._max_concurrency)
            tasks: set[asyncio.Task[None]] = set()
            writer = asyncio.create_task(_send_messages(websocket, outgoing))
            sources = [
                asyncio.create_task(
                    _send_events(event, resolver, outgoing, self._codec)
                )
                for event in self._channel.protocol.notifications
            ]
            try:
                while True:
                    message = await websocket.receive()
                    if message.get("type") == "websocket.disconnect":
                        break
                    payload = message.get("text")
                    if payload is None:
                        payload = message.get("bytes")
                    if not isinstance(payload, str | bytes | bytearray):
                        continue
                    await concurrency.acquire()
                    task = asyncio.create_task(
                        _serve_message(server, payload, outgoing, concurrency)
                    )
                    tasks.add(task)
                    task.add_done_callback(tasks.discard)
            finally:
                for task in (*tasks, *sources, writer):
                    task.cancel()
                with suppress(asyncio.CancelledError):
                    await asyncio.gather(*tasks, *sources, writer)


async def _send_events(
    event: RpcNotificationDefinition,
    resolver: RpcResolver,
    outgoing: asyncio.Queue[str],
    codec: RpcCodec,
) -> None:
    if event.function is None:
        raise TypeError(f"Event {event.name!r} has no source")
    arguments = {
        parameter.name: await resolver.resolve(parameter.dependency)
        for parameter in event.injected_parameters
    }
    adapter = TypeAdapter(event.payload)
    async for payload in event.function(**arguments):
        validated = adapter.validate_python(payload)
        message = RpcNotification._with_payload_annotation(
            event.name,
            validated,
            event.payload,
        )
        await outgoing.put(codec.encode(message))


async def _serve_message(
    server: Any,
    payload: str | bytes | bytearray,
    outgoing: asyncio.Queue[str],
    concurrency: asyncio.Semaphore,
) -> None:
    try:
        response = await server.handle_json(payload)
        if response is not None:
            await outgoing.put(response)
    finally:
        concurrency.release()


async def _send_messages(
    websocket: WebSocketConnection,
    outgoing: asyncio.Queue[str],
) -> None:
    while True:
        await websocket.send_text(await outgoing.get())
