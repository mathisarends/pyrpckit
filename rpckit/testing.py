import asyncio
import inspect
import json
from collections.abc import Callable, Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from pydantic import TypeAdapter, ValidationError

from rpckit.codec import RpcCodec
from rpckit.connection import (
    RpcConnectionClose,
    RpcDisconnect,
    RpcHandshake,
    RpcLimits,
    RpcRejection,
    RpcRejections,
)
from rpckit.dependencies import RpcResolverLike
from rpckit.envelopes import RpcFailure, RpcSuccess
from rpckit.errors import (
    RpcError,
    RpcInternalError,
    RpcInvalidParamsError,
    RpcMethodNotFoundError,
)
from rpckit.protocol import RpcClientMethod
from rpckit.server import RpcErrorMapper
from rpckit.service import RpcEndpoint, RpcService, RpcStreamEndpoint
from rpckit.streams import RpcInputEndMessage

_DISCONNECT = object()
_CLOSED = object()

__all__ = [
    "InMemorySocket",
    "RpcTestClient",
    "RpcTestConnectionClosed",
    "RpcTestError",
    "RpcTestStream",
]

type RpcClientMethodHandler = Callable[..., Any]


class InMemorySocket:
    def __init__(
        self,
        path: str,
        *,
        headers: Mapping[str, str] | None = None,
        query_params: Mapping[str, str] | None = None,
        subprotocols: Iterable[str] = (),
    ) -> None:
        self._handshake = RpcHandshake(
            path=path,
            headers=headers or {},
            query_params=query_params or {},
            subprotocols=tuple(subprotocols),
        )
        self._incoming: asyncio.Queue[Any] = asyncio.Queue()
        self._outgoing: asyncio.Queue[Any] = asyncio.Queue()
        self.accepted = False
        self.subprotocol: str | None = None
        self.rejection: tuple[RpcRejection, str] | None = None
        self.closed: tuple[RpcConnectionClose, str] | None = None

    @property
    def handshake(self) -> RpcHandshake:
        return self._handshake

    async def accept(self, subprotocol: str | None = None) -> None:
        self.accepted = True
        self.subprotocol = subprotocol

    async def reject(
        self,
        rejection: RpcRejection,
        reason: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.rejection = (rejection, reason)
        await self._outgoing.put(_DISCONNECT)

    async def receive(self) -> str | bytes:
        value = await self._incoming.get()
        if isinstance(value, RpcDisconnect):
            raise value
        return value

    async def send(self, message: str) -> None:
        await self._outgoing.put(message)

    async def send_bytes(self, data: bytes) -> None:
        await self._outgoing.put(data)

    async def close(self, close: RpcConnectionClose, reason: str) -> None:
        self.closed = (close, reason)
        await self._outgoing.put(_DISCONNECT)

    async def client_send(self, message: str | bytes) -> None:
        await self._incoming.put(message)

    async def client_receive(self) -> str | bytes:
        value = await self._outgoing.get()
        if value is _DISCONNECT:
            raise RpcDisconnect()
        return value

    async def client_disconnect(
        self,
        code: RpcConnectionClose | int = RpcConnectionClose.NORMAL,
        reason: str = "",
    ) -> None:
        await self._incoming.put(RpcDisconnect(code, reason))


@dataclass(frozen=True, slots=True)
class RpcTestError(Exception):
    rpc_code: int
    code: str
    message: str
    details: Any = None


class RpcTestConnectionClosed(Exception):
    pass


class RpcTestClient:
    def __init__(
        self,
        service: RpcService,
        path: str,
        *,
        headers: Mapping[str, str] | None = None,
        subprotocols: Iterable[str] = (),
        resolver: RpcResolverLike | None = None,
        context: object | Mapping[type[Any], object] | None = None,
        error_mapper: RpcErrorMapper | None = None,
        limits: RpcLimits | None = None,
        rejections: RpcRejections | None = None,
        client_methods: Mapping[str | RpcClientMethod[Any, Any], RpcClientMethodHandler]
        | None = None,
    ) -> None:
        self.service = service
        self.socket = InMemorySocket(path, headers=headers, subprotocols=subprotocols)
        self.resolver = resolver
        self.context = context
        self.error_mapper = error_mapper
        self.limits = limits
        self.rejections = rejections
        self._task: asyncio.Task | None = None
        self._reader: asyncio.Task | None = None
        self._id = 0
        self._notifications: asyncio.Queue[object] = asyncio.Queue()
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._answers: set[asyncio.Task] = set()
        matched = service.match(path)
        endpoint = None if matched is None else matched[0]
        self._stream = isinstance(endpoint, RpcStreamEndpoint)
        self._input = self._stream and endpoint.stream.has_input
        declared = (
            {
                client_method.name: client_method
                for client_method in endpoint.protocol.client_methods
            }
            if isinstance(endpoint, RpcEndpoint)
            else {}
        )
        self._client_methods: dict[
            str, tuple[RpcClientMethod[Any, Any], RpcClientMethodHandler]
        ] = {}
        self._declared_client_methods = declared
        for key, handler in (client_methods or {}).items():
            self.handle(key, handler)

    def handle(
        self,
        method: str | RpcClientMethod[Any, Any],
        handler: RpcClientMethodHandler,
    ) -> None:
        name = method.name if isinstance(method, RpcClientMethod) else method
        if name not in self._declared_client_methods:
            raise ValueError(
                f"RPC client method {name!r} is not declared on "
                f"{self.socket.handshake.path!r}"
            )
        self._client_methods[name] = (self._declared_client_methods[name], handler)

    async def __aenter__(self):
        self._task = asyncio.create_task(
            self.service.serve(
                self.socket,
                resolver=self.resolver,
                context=self.context,
                error_mapper=self.error_mapper,
                limits=self.limits,
                rejections=self.rejections,
            )
        )
        if not self._stream:
            self._reader = asyncio.create_task(self._read())
        await asyncio.sleep(0)
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.socket.client_disconnect()
        if self._task is not None:
            with suppress(asyncio.CancelledError):
                await self._task
        tasks = (*self._answers, *((self._reader,) if self._reader else ()))
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def request(
        self, method: str, params: Mapping[str, Any] | None = None
    ) -> Any:
        if self._stream:
            raise TypeError("request() is unavailable for stream endpoints")
        if self._reader is not None and self._reader.done():
            raise self._connection_closed()
        self._id += 1
        request_id = self._id
        response = asyncio.get_running_loop().create_future()
        self._pending[request_id] = response
        try:
            await self.socket.client_send(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "method": method,
                        "params": dict(params or {}),
                    }
                )
            )
            value = await response
        finally:
            self._pending.pop(request_id, None)
        if "error" in value:
            item = value["error"]
            data = item.get("data", {})
            raise RpcTestError(
                item["code"],
                data.get("code", ""),
                item["message"],
                data.get("details"),
            )
        return value["result"]

    async def notify(
        self, method: str, params: Mapping[str, Any] | None = None
    ) -> None:
        await self.socket.client_send(
            json.dumps(
                {"jsonrpc": "2.0", "method": method, "params": dict(params or {})}
            )
        )

    async def next_notification(
        self, *, timeout: float | None = None
    ) -> tuple[str, Any]:
        async with asyncio.timeout(timeout):
            item = await self._notifications.get()
        if item is _CLOSED:
            self._notifications.put_nowait(_CLOSED)
            raise self._connection_closed()
        return item  # type: ignore[return-value]

    async def _read(self) -> None:
        try:
            while True:
                value = json.loads(await self.socket.client_receive())
                if not isinstance(value, dict):
                    continue
                if "method" in value and "id" in value:
                    task = asyncio.create_task(self._answer(value))
                    self._answers.add(task)
                    task.add_done_callback(self._answers.discard)
                elif "method" in value:
                    await self._notifications.put((value["method"], value["params"]))
                else:
                    response = self._pending.get(value.get("id"))
                    if response is not None and not response.done():
                        response.set_result(value)
        except RpcDisconnect:
            error = self._connection_closed()
            for response in self._pending.values():
                if not response.done():
                    response.set_exception(error)
            await self._notifications.put(_CLOSED)

    async def _answer(self, message: dict[str, Any]) -> None:
        response = await self._client_method_response(message)
        await self.socket.client_send(RpcCodec().encode(response))

    async def _client_method_response(
        self, message: dict[str, Any]
    ) -> RpcSuccess | RpcFailure:
        request_id = message["id"]
        registered = self._client_methods.get(message["method"])
        if registered is None:
            return RpcFailure.from_error(
                request_id, RpcMethodNotFoundError(message["method"])
            )
        client_method, handler = registered
        arguments: tuple[Any, ...] = ()
        if client_method.params is not None:
            try:
                params = TypeAdapter(client_method.params).validate_python(
                    message.get("params", {})
                )
            except ValidationError as error:
                return RpcFailure.from_error(
                    request_id, RpcInvalidParamsError.from_validation_error(error)
                )
            arguments = (params,)
        try:
            result = handler(*arguments)
            if inspect.isawaitable(result):
                result = await result
        except RpcError as error:
            return RpcFailure.from_error(request_id, error)
        except Exception:
            return RpcFailure.from_error(request_id, RpcInternalError())
        return RpcSuccess._with_result_annotation(
            request_id, result, client_method.result
        )

    def _connection_closed(self) -> "RpcTestConnectionClosed":
        return RpcTestConnectionClosed(self.socket.rejection or self.socket.closed)

    async def next_frame(self) -> bytes:
        if not self._stream:
            raise TypeError("next_frame() is only available for stream endpoints")
        try:
            value = await self.socket.client_receive()
        except RpcDisconnect as error:
            raise RpcTestConnectionClosed(self.socket.closed) from error
        if not isinstance(value, bytes):
            raise TypeError("Expected a binary frame")
        return value

    async def send_frame(self, data: bytes | bytearray | memoryview) -> None:
        if not self._input:
            raise TypeError("send_frame() is only available for streams with input")
        await self.socket.client_send(bytes(data))

    async def end_input(self) -> None:
        if not self._input:
            raise TypeError("end_input() is only available for streams with input")
        await self.socket.client_send(RpcInputEndMessage(type="end").model_dump_json())

    async def closed(self) -> tuple[RpcConnectionClose, str] | None:
        """Wait until the server finished the connection and return its close."""
        if self._task is not None:
            with suppress(asyncio.CancelledError):
                await self._task
        return self.socket.closed


class RpcTestStream(RpcTestClient):
    def __init__(self, service: RpcService, path: str, **kwargs: Any) -> None:
        super().__init__(service, path, **kwargs)
        if not self._stream:
            raise TypeError(f"{path!r} is not a binary stream endpoint")
