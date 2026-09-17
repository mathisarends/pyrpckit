from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol


class RpcRejection(StrEnum):
    UNAUTHORIZED = "unauthorized"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    PROTOCOL_ERROR = "protocol_error"
    UNAVAILABLE = "unavailable"
    INTERNAL_ERROR = "internal_error"


class RpcConnectionClose(StrEnum):
    NORMAL = "normal"
    SHUTDOWN = "shutdown"
    PROTOCOL_ERROR = "protocol_error"
    POLICY_VIOLATION = "policy_violation"
    MESSAGE_TOO_BIG = "message_too_big"
    INTERNAL_ERROR = "internal_error"


class ConnectionRejected(Exception):
    def __init__(self, rejection: RpcRejection, reason: str = "") -> None:
        if not isinstance(rejection, RpcRejection):
            raise TypeError("rejection must be an RpcRejection")
        self.rejection = rejection
        self.reason = reason or rejection.value.replace("_", " ").capitalize()
        super().__init__(self.reason)


class RpcDisconnect(Exception):
    pass


@dataclass(frozen=True, slots=True)
class RpcHandshake:
    path: str
    headers: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    query_params: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({})
    )
    path_params: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    subprotocols: tuple[str, ...] = ()
    client: tuple[str, int] | None = None


class RpcSocket(Protocol):
    @property
    def handshake(self) -> RpcHandshake: ...
    async def accept(self, subprotocol: str | None = None) -> None: ...
    async def reject(self, rejection: RpcRejection, reason: str) -> None: ...
    async def receive(self) -> str | bytes: ...
    async def send(self, message: str) -> None: ...
    async def send_bytes(self, data: bytes) -> None: ...
    async def close(self, close: RpcConnectionClose, reason: str) -> None: ...


@dataclass(frozen=True, slots=True)
class RpcLimits:
    max_concurrency: int = 32
    max_queue_size: int = 128
    max_message_bytes: int | None = 1_048_576

    def __post_init__(self) -> None:
        if self.max_concurrency < 1 or self.max_queue_size < 1:
            raise ValueError("RPC limits must be at least 1")
        if self.max_message_bytes is not None and self.max_message_bytes < 1:
            raise ValueError("max_message_bytes must be at least 1 or None")


class _Headers(Mapping[str, str]):
    def __init__(self, values: Mapping[str, str]) -> None:
        self._values = {key.lower(): value for key, value in values.items()}

    def __getitem__(self, key: str) -> str:
        return self._values[key.lower()]

    def __iter__(self):
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)


class RpcConnection:
    __slots__ = (
        "_accepted",
        "_closed",
        "_endpoint",
        "_handshake",
        "_on_close",
        "_path_params",
    )

    def __init__(self) -> None:
        raise TypeError("RpcConnection instances are created by pyrpckit")

    @classmethod
    def _create(
        cls, endpoint, socket: RpcSocket, path_params: Mapping[str, str] | None = None
    ):
        self = cls.__new__(cls)
        self._endpoint = endpoint.name
        self._handshake = socket.handshake
        self._path_params = MappingProxyType(
            dict(path_params or socket.handshake.path_params)
        )
        self._accepted = False
        self._closed = False
        self._on_close = None
        return self

    endpoint = property(lambda self: self._endpoint)
    path = property(lambda self: self._handshake.path)
    path_params = property(lambda self: self._path_params)
    query_params = property(
        lambda self: MappingProxyType(dict(self._handshake.query_params))
    )
    headers = property(lambda self: _Headers(self._handshake.headers))
    subprotocols = property(lambda self: self._handshake.subprotocols)
    client = property(lambda self: self._handshake.client)
    closed = property(lambda self: self._closed)

    async def close(
        self, close: RpcConnectionClose = RpcConnectionClose.NORMAL, *, reason: str = ""
    ) -> None:
        if not self._accepted:
            raise RuntimeError(
                "RpcConnection.close() is only valid after the connection was "
                "accepted; raise ConnectionRejected in connect hooks"
            )
        if self._on_close is not None:
            self._on_close(close, reason)
