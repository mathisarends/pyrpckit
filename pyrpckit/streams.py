import asyncio
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict

from pyrpckit.connection import RpcConnection, RpcDisconnect, RpcSocket

_END = object()


class RpcStreamDirection(StrEnum):
    SERVER_TO_CLIENT = "server-to-client"
    CLIENT_TO_SERVER = "client-to-server"
    BIDIRECTIONAL = "bidirectional"


class RpcInputEndMessage(BaseModel):
    """The text message a client sends to end its input; output keeps flowing."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["end"]


class RpcInputEnded(Exception):
    """Raised by ``RpcBinaryInput.receive()`` after the client ended its input."""


class RpcBinaryInput:
    """Frames the client sends; iteration ends when the client ends its input."""

    __slots__ = ("_ended", "_queue")

    def __init__(self) -> None:
        raise TypeError("RpcBinaryInput instances are created by pyrpckit")

    @classmethod
    def _create(cls, max_queue_size: int) -> Self:
        self = cls.__new__(cls)
        self._queue = asyncio.Queue[object](max_queue_size)
        self._ended = False
        return self

    @property
    def ended(self) -> bool:
        return self._ended

    async def receive(self) -> bytes:
        if self._ended:
            raise RpcInputEnded("The client ended its binary input")
        item = await self._queue.get()
        if item is _END:
            self._ended = True
            raise RpcInputEnded("The client ended its binary input")
        return item  # type: ignore[return-value]

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> bytes:
        try:
            return await self.receive()
        except RpcInputEnded:
            raise StopAsyncIteration from None

    async def _put(self, frame: bytes) -> None:
        await self._queue.put(frame)

    async def _end(self) -> None:
        await self._queue.put(_END)


class RpcBinaryOutput:
    """Frames the server sends; waits while the socket applies backpressure."""

    __slots__ = ("_connection", "_socket")

    def __init__(self) -> None:
        raise TypeError("RpcBinaryOutput instances are created by pyrpckit")

    @classmethod
    def _create(cls, socket: RpcSocket, connection: RpcConnection) -> Self:
        self = cls.__new__(cls)
        self._socket = socket
        self._connection = connection
        return self

    async def send(self, frame: bytes | bytearray | memoryview) -> None:
        if not isinstance(frame, bytes | bytearray | memoryview):
            raise TypeError("RpcBinaryOutput.send() expects bytes")
        if self._connection.closed or self._connection.close_code is not None:
            raise RpcDisconnect("The binary stream is closed")
        await self._socket.send_bytes(bytes(frame))
