import asyncio
import inspect
from collections.abc import AsyncIterator, Callable, Iterable, Mapping
from contextlib import suppress
from typing import Any, Protocol, get_type_hints

from pyrpckit.app import RpcApp
from pyrpckit.codec import RpcCodec
from pyrpckit.dependencies import (
    EmptyResolver,
    RpcResolver,
    connection_scope,
    injected_parameter,
)
from pyrpckit.envelopes import RpcNotification
from pyrpckit.errors import ProtocolDefinitionError
from pyrpckit.notifications import RpcOutgoingMessage
from pyrpckit.server import RpcErrorMapper

type RpcNotificationSource = Callable[..., AsyncIterator[RpcOutgoingMessage]]


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
        notifications: Iterable[RpcNotificationSource] = (),
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
        self._notifications = tuple(notifications)
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
                    self._send_notifications(source, resolver, outgoing)
                )
                for source in self._notifications
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
        source: RpcNotificationSource,
        resolver: RpcResolver,
        outgoing: asyncio.Queue[str],
    ) -> None:
        arguments = await _notification_arguments(source, resolver)
        async for notification in source(**arguments):
            if not isinstance(notification, RpcNotification):
                raise TypeError(
                    f"Notification source {source.__qualname__} yielded "
                    f"{type(notification).__name__}, expected RpcOutgoingMessage"
                )
            await outgoing.put(self._codec.encode(notification))


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


async def _notification_arguments(
    source: RpcNotificationSource,
    resolver: RpcResolver,
) -> dict[str, object]:
    if not inspect.isasyncgenfunction(source):
        raise ProtocolDefinitionError(
            f"Notification source {source.__qualname__} must be an async generator"
        )
    hints = get_type_hints(source, include_extras=True)
    arguments: dict[str, object] = {}
    for parameter in inspect.signature(source).parameters.values():
        annotation = hints.get(parameter.name)
        if annotation is None:
            raise ProtocolDefinitionError(
                f"Notification source parameter {parameter.name!r} needs an annotation"
            )
        injected = injected_parameter(parameter.name, annotation)
        if injected is None:
            raise ProtocolDefinitionError(
                f"Notification source parameter {parameter.name!r} must use Inject[T]"
            )
        arguments[parameter.name] = await resolver.resolve(injected.dependency)
    return arguments
