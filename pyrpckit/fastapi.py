from collections.abc import Mapping, Sequence
from typing import Any

from fastapi import APIRouter, Response, WebSocket, params
from starlette.websockets import WebSocketDisconnect

from pyrpckit.connection import (
    RpcConnectionClose,
    RpcDisconnect,
    RpcHandshake,
    RpcLimits,
    RpcRejection,
)
from pyrpckit.dependencies import RpcResolverLike
from pyrpckit.server import RpcErrorMapper
from pyrpckit.service import RpcEndpoint, RpcService
from pyrpckit.websocket import CLOSE_CODES, REJECTION_CLOSE_CODES, close_reason

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

    handshake = property(lambda self: self._handshake)

    async def accept(self, subprotocol: str | None = None) -> None:
        await self._websocket.accept(subprotocol=subprotocol)

    async def reject(self, rejection: RpcRejection, reason: str) -> None:
        if "websocket.http.response" in self._websocket.scope.get("extensions", {}):
            await self._websocket.send_denial_response(
                Response(
                    reason, status_code=_HTTP_STATUS[rejection], media_type="text/plain"
                )
            )
        else:
            await self._websocket.close(
                REJECTION_CLOSE_CODES[rejection], close_reason(reason)
            )

    async def receive(self) -> str | bytes:
        message = await self._websocket.receive()
        if message["type"] == "websocket.disconnect":
            raise RpcDisconnect(str(message.get("reason", "")))
        value = message.get("text")
        return value if value is not None else message.get("bytes", b"")

    async def send(self, message: str) -> None:
        try:
            await self._websocket.send_text(message)
        except (WebSocketDisconnect, RuntimeError) as error:
            raise RpcDisconnect() from error

    async def send_bytes(self, data: bytes) -> None:
        try:
            await self._websocket.send_bytes(data)
        except (WebSocketDisconnect, RuntimeError) as error:
            raise RpcDisconnect() from error

    async def close(self, close: RpcConnectionClose, reason: str) -> None:
        await self._websocket.close(CLOSE_CODES[close], close_reason(reason))


def create_router(
    service: RpcService,
    *,
    resolver: RpcResolverLike | None = None,
    context: object | Mapping[type[Any], object] | None = None,
    error_mapper: RpcErrorMapper | None = None,
    limits: RpcLimits | None = None,
    prefix: str = "",
    dependencies: Sequence[params.Depends] | None = None,
) -> APIRouter:
    service.freeze()
    router = APIRouter(prefix=prefix, dependencies=dependencies)
    for endpoint in service.endpoints:
        if isinstance(endpoint, RpcEndpoint):

            async def handler(websocket: WebSocket, endpoint=endpoint) -> None:
                await endpoint.serve(
                    FastApiSocket(websocket),
                    resolver=resolver,
                    context=context,
                    error_mapper=error_mapper,
                    limits=limits,
                )
        else:

            async def handler(websocket: WebSocket, endpoint=endpoint) -> None:
                await endpoint.serve(
                    FastApiSocket(websocket),
                    resolver=resolver,
                    context=context,
                    limits=limits,
                )

        router.add_api_websocket_route(endpoint.path, handler, name=endpoint.name)
    return router
