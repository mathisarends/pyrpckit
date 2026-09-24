import asyncio
from collections.abc import Callable, Mapping
from typing import Any

try:
    from fastapi import APIRouter, Response, WebSocket, WebSocketDisconnect
except ImportError as error:
    raise ModuleNotFoundError(
        "rpckit.fastapi requires the 'fastapi' extra; install pyrpckit[fastapi]",
        name="fastapi",
    ) from error

from rpckit.connection import (
    RpcBeforeAccept,
    RpcConnectionClose,
    RpcDisconnect,
    RpcHandshake,
    RpcLimits,
    RpcRejection,
)
from rpckit.dependencies import RpcResolverLike
from rpckit.server import RpcErrorMapper
from rpckit.service import RpcEndpoint, RpcService, RpcStreamEndpoint
from rpckit.websocket import CLOSE_CODES, REJECTION_CLOSE_CODES, close_reason

type FastApiResolverFactory = Callable[[WebSocket], RpcResolverLike]

_HTTP_STATUS = {
    RpcRejection.UNAUTHORIZED: 401,
    RpcRejection.FORBIDDEN: 403,
    RpcRejection.NOT_FOUND: 404,
    RpcRejection.PROTOCOL_ERROR: 400,
    RpcRejection.UNAVAILABLE: 503,
    RpcRejection.INTERNAL_ERROR: 500,
}


class FastApiSocket:
    def __init__(self, websocket: WebSocket) -> None:
        self._websocket = websocket
        client = websocket.client
        self._handshake = RpcHandshake(
            path=websocket.url.path,
            headers=dict(websocket.headers),
            query_params=dict(websocket.query_params),
            path_params=dict(websocket.path_params),
            subprotocols=tuple(websocket.scope.get("subprotocols", ())),
            client=None if client is None else (client.host, client.port),
        )

    @property
    def handshake(self) -> RpcHandshake:
        return self._handshake

    async def accept(self, subprotocol: str | None = None) -> None:
        await self._websocket.accept(subprotocol=subprotocol)

    async def reject(
        self,
        rejection: RpcRejection,
        reason: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        if "websocket.http.response" in self._websocket.scope.get("extensions", {}):
            await self._websocket.send_denial_response(
                Response(
                    reason,
                    status_code=_HTTP_STATUS[rejection],
                    media_type="text/plain",
                    headers=dict(headers or {}),
                )
            )
        else:
            await self._websocket.close(
                REJECTION_CLOSE_CODES[rejection], close_reason(reason)
            )

    async def receive(self) -> str | bytes:
        message = await self._websocket.receive()
        if message["type"] == "websocket.disconnect":
            raise RpcDisconnect(
                message.get("code") or 1000, str(message.get("reason", ""))
            )
        value = message.get("text")
        return value if value is not None else message.get("bytes", b"")

    async def send(self, message: str) -> None:
        try:
            await self._websocket.send_text(message)
        except WebSocketDisconnect as error:
            raise RpcDisconnect(error.code, error.reason or "") from error

    async def send_bytes(self, data: bytes) -> None:
        try:
            await self._websocket.send_bytes(data)
        except WebSocketDisconnect as error:
            raise RpcDisconnect(error.code, error.reason or "") from error

    async def close(self, close: RpcConnectionClose, reason: str) -> None:
        await self._websocket.close(CLOSE_CODES[close], close_reason(reason))


def create_router(
    service: RpcService,
    *,
    resolver: RpcResolverLike | None = None,
    resolver_factory: FastApiResolverFactory | None = None,
    context: object | Mapping[type[Any], object] | None = None,
    error_mapper: RpcErrorMapper | None = None,
    limits: RpcLimits | None = None,
    before_accept: RpcBeforeAccept | None = None,
) -> APIRouter:
    if resolver is not None and resolver_factory is not None:
        raise ValueError("resolver and resolver_factory are mutually exclusive")
    service.freeze()
    router = APIRouter()
    for endpoint in service.endpoints:
        handler = _create_handler(
            endpoint,
            resolver=resolver,
            resolver_factory=resolver_factory,
            context=context,
            error_mapper=error_mapper,
            limits=limits,
            before_accept=before_accept,
        )
        router.add_api_websocket_route(endpoint.path, handler, name=endpoint.name)
    return router


def _create_handler(
    endpoint: RpcEndpoint | RpcStreamEndpoint,
    *,
    resolver: RpcResolverLike | None,
    resolver_factory: FastApiResolverFactory | None,
    context: object | Mapping[type[Any], object] | None,
    error_mapper: RpcErrorMapper | None,
    limits: RpcLimits | None,
    before_accept: RpcBeforeAccept | None,
):
    async def handler(websocket: WebSocket) -> None:
        connection_resolver = (
            resolver_factory(websocket) if resolver_factory is not None else resolver
        )
        await serve_websocket(
            endpoint,
            websocket,
            resolver=connection_resolver,
            context=context,
            error_mapper=error_mapper,
            limits=limits,
            before_accept=before_accept,
        )

    return handler


async def serve_websocket(
    endpoint: RpcEndpoint | RpcStreamEndpoint,
    websocket: WebSocket,
    *,
    resolver: RpcResolverLike | None = None,
    context: object | Mapping[type[Any], object] | None = None,
    error_mapper: RpcErrorMapper | None = None,
    limits: RpcLimits | None = None,
    before_accept: RpcBeforeAccept | None = None,
) -> None:
    socket = FastApiSocket(websocket)
    try:
        if isinstance(endpoint, RpcEndpoint):
            await endpoint.serve(
                socket,
                resolver=resolver,
                context=context,
                error_mapper=error_mapper,
                limits=limits,
                before_accept=before_accept,
            )
        else:
            await endpoint.serve(
                socket,
                resolver=resolver,
                context=context,
                limits=limits,
                before_accept=before_accept,
                error_mapper=error_mapper,
            )
    except asyncio.CancelledError:
        # Starlette cancels its WebSocket task after websocket.disconnect.
        return
