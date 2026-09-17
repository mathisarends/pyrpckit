import asyncio
import json
from collections.abc import Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from pyrpckit.connection import (
    RpcConnectionClose,
    RpcDisconnect,
    RpcHandshake,
    RpcLimits,
    RpcRejection,
)
from pyrpckit.dependencies import RpcResolverLike
from pyrpckit.server import RpcErrorMapper
from pyrpckit.service import RpcService, RpcStreamEndpoint

_DISCONNECT = object()


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

    handshake = property(lambda self: self._handshake)

    async def accept(self, subprotocol: str | None = None) -> None:
        self.accepted = True
        self.subprotocol = subprotocol

    async def reject(self, rejection: RpcRejection, reason: str) -> None:
        self.rejection = (rejection, reason)
        await self._outgoing.put(_DISCONNECT)

    async def receive(self) -> str | bytes:
        value = await self._incoming.get()
        if value is _DISCONNECT:
            raise RpcDisconnect()
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

    async def client_disconnect(self, reason: str = "") -> None:
        await self._incoming.put(_DISCONNECT)


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
    ) -> None:
        self.service = service
        self.socket = InMemorySocket(path, headers=headers, subprotocols=subprotocols)
        self.resolver = resolver
        self.context = context
        self.error_mapper = error_mapper
        self.limits = limits
        self._task: asyncio.Task | None = None
        self._id = 0
        self._notifications: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
        matched = service.match(path)
        self._stream = matched is not None and isinstance(matched[0], RpcStreamEndpoint)

    async def __aenter__(self):
        self._task = asyncio.create_task(
            self.service.serve(
                self.socket,
                resolver=self.resolver,
                context=self.context,
                error_mapper=self.error_mapper,
                limits=self.limits,
            )
        )
        await asyncio.sleep(0)
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.socket.client_disconnect()
        if self._task is not None:
            with suppress(asyncio.CancelledError):
                await self._task

    async def request(
        self, method: str, params: Mapping[str, Any] | None = None
    ) -> Any:
        if self._stream:
            raise TypeError("request() is unavailable for stream endpoints")
        self._id += 1
        request_id = self._id
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
        while True:
            try:
                raw = await self.socket.client_receive()
            except RpcDisconnect as error:
                raise RpcTestConnectionClosed(
                    self.socket.rejection or self.socket.closed
                ) from error
            value = json.loads(raw)
            if "id" not in value:
                await self._notifications.put((value["method"], value["params"]))
                continue
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

    async def next_notification(self) -> tuple[str, Any]:
        if not self._notifications.empty():
            return await self._notifications.get()
        raw = await self.socket.client_receive()
        value = json.loads(raw)
        return value["method"], value["params"]

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
