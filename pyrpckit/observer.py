import logging
from dataclasses import dataclass
from typing import Protocol

from pyrpckit.connection import RpcConnection, RpcConnectionClose
from pyrpckit.constants import LOGGER_NAME
from pyrpckit.envelopes import RpcRequestId

logger = logging.getLogger(LOGGER_NAME)


@dataclass(frozen=True, slots=True)
class RpcRequestContext:
    raw_request: object
    method: str | None
    request_id: RpcRequestId
    notification: bool
    connection: RpcConnection | None = None


@dataclass(frozen=True, slots=True)
class RpcResponseContext:
    request: RpcRequestContext
    response: object | None
    duration: float
    error: BaseException | None = None


@dataclass(frozen=True, slots=True)
class RpcConnectionContext:
    connection: RpcConnection
    close_code: RpcConnectionClose | None
    close_reason: str
    duration: float
    raw_close_code: int | None = None


class RpcObserverLike(Protocol):
    async def request_started(self, context: RpcRequestContext) -> None: ...

    async def request_finished(self, context: RpcResponseContext) -> None: ...

    async def connection_closed(self, context: RpcConnectionContext) -> None: ...


class RpcObserver:
    """Subclass and override only the callbacks needed by an application."""

    async def request_started(self, context: RpcRequestContext) -> None:
        pass

    async def request_finished(self, context: RpcResponseContext) -> None:
        pass

    async def connection_opened(self, connection: RpcConnection) -> None:
        pass

    async def connection_closed(self, context: RpcConnectionContext) -> None:
        pass

    async def notification_sent(self, name: str, size: int) -> None:
        pass

    async def slow_consumer_closed(self, connection: RpcConnection) -> None:
        pass

    async def stream_frame_sent(self, connection: RpcConnection, size: int) -> None:
        pass

    async def stream_frame_received(self, connection: RpcConnection, size: int) -> None:
        pass


async def notify_observer(
    observer: RpcObserverLike | None,
    method: str,
    *args: object,
) -> None:
    if observer is None:
        return
    callback = getattr(observer, method, None)
    if callback is None:
        return
    try:
        await callback(*args)
    except Exception:
        logger.exception("RPC observer %s callback failed", method)
