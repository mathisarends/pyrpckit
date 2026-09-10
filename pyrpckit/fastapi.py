import asyncio
from collections.abc import Mapping
from contextlib import suppress
from typing import Any, Protocol

from pydantic import TypeAdapter

from pyrpckit.app import RpcApp
from pyrpckit.codec import RpcCodec
from pyrpckit.dependencies import (
    EmptyResolver,
    RpcResolver,
    connection_scope,
)
from pyrpckit.envelopes import RpcNotification
from pyrpckit.protocol import RpcNotificationDefinition
from pyrpckit.server import RpcErrorMapper


class WebSocket(Protocol):
    async def accept(self, subprotocol: str | None = None) -> None: ...

    async def receive(self) -> Mapping[str, Any]: ...

    async def send_text(self, data: str) -> None: ...


class RpcWebSocketApp:
    """Run an ``RpcApp`` on a FastAPI/Starlette WebSocket."""

    def __init__(
        self,
        app: RpcApp,
        *,
        resolver: RpcResolver | None = None,
        error_mapper: RpcErrorMapper | None = None,
        max_concurrency: int = 32,
        max_queue_size: int = 128,
        subprotocol: str | None = None,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be at least 1")
        if max_queue_size < 1:
            raise ValueError("max_queue_size must be at least 1")
        self._app = app
        self._resolver = resolver or EmptyResolver()
        self._error_mapper = error_mapper
        self._max_concurrency = max_concurrency
        self._max_queue_size = max_queue_size
        self._subprotocol = subprotocol
        self._codec = RpcCodec()

    @property
    def app(self) -> RpcApp:
        return self._app

    async def serve(
        self,
        websocket: WebSocket,
        *,
        context: object | Mapping[type[Any], object] | None = None,
    ) -> None:
        """Accept and serve one WebSocket connection until it disconnects."""
        async with connection_scope(self._resolver, context) as resolver:
            server = self._app.server(
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
                    self._send_notifications(notification, resolver, outgoing)
                )
                for notification in self._app.protocol.notifications
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

    async def _send_notifications(
        self,
        notification: RpcNotificationDefinition,
        resolver: RpcResolver,
        outgoing: asyncio.Queue[str],
    ) -> None:
        if notification.function is None:
            raise TypeError(f"Notification {notification.name!r} has no source")
        arguments = {
            parameter.name: await resolver.resolve(parameter.dependency)
            for parameter in notification.injected_parameters
        }
        adapter = TypeAdapter(notification.payload)
        async for payload in notification.function(**arguments):
            validated = adapter.validate_python(payload)
            message = RpcNotification._with_payload_annotation(
                notification.name,
                validated,
                notification.payload,
            )
            await outgoing.put(self._codec.encode(message))


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
    websocket: WebSocket,
    outgoing: asyncio.Queue[str],
) -> None:
    while True:
        await websocket.send_text(await outgoing.get())
