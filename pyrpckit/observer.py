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


@dataclass(frozen=True, slots=True)
class RpcResponseContext:
    request: RpcRequestContext
    response: object | None
    duration: float
    error: BaseException | None = None


@dataclass(frozen=True, slots=True)
class RpcConnectionContext:
    connection: RpcConnection
    close_code: RpcConnectionClose | int | None
    close_reason: str
    duration: float


class RpcObserver(Protocol):
    async def request_started(self, context: RpcRequestContext) -> None: ...

    async def request_finished(self, context: RpcResponseContext) -> None: ...

    async def connection_closed(self, context: RpcConnectionContext) -> None: ...


async def notify_observer(
    observer: RpcObserver | None,
    method: str,
    context: object,
) -> None:
    if observer is None:
        return
    try:
        callback = getattr(observer, method)
        await callback(context)
    except Exception:
        logger.exception("RPC observer %s callback failed", method)
