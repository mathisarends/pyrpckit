import asyncio
import inspect
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from typing import Annotated, Any, get_args, get_origin

try:
    from fastapi import (
        APIRouter,
        Depends,
        Response,
        WebSocket,
        WebSocketDisconnect,
    )
    from fastapi.websockets import WebSocketState
except ImportError as error:
    raise ModuleNotFoundError(
        "rpckit.fastapi requires the 'fastapi' extra; install pyrpckit[fastapi]",
        name="fastapi",
    ) from error

from rpckit.connection import (
    REJECTION_CLOSES,
    RpcBeforeAccept,
    RpcConnectionClose,
    RpcDisconnect,
    RpcHandshake,
    RpcLimits,
    RpcRejection,
    RpcRejections,
)
from rpckit.dependencies import RpcResolverLike, context_values
from rpckit.runtime import _rejection
from rpckit.server import RpcErrorMapper
from rpckit.service import RpcEndpoint, RpcService, RpcStreamEndpoint
from rpckit.websocket import CLOSE_CODES, REJECTION_CLOSE_CODES, close_reason

type FastApiResolverFactory = Callable[[WebSocket], RpcResolverLike]
type RpcContextFunction = Callable[..., Awaitable[object]]

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
        await _reject(self._websocket, rejection, reason, headers=headers)

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


async def _reject(
    websocket: WebSocket,
    rejection: RpcRejection,
    reason: str,
    *,
    headers: Mapping[str, str] | None = None,
) -> None:
    if "websocket.http.response" in websocket.scope.get("extensions", {}):
        await websocket.send_denial_response(
            Response(
                reason,
                status_code=_HTTP_STATUS[rejection],
                media_type="text/plain",
                headers=dict(headers or {}),
            )
        )
    else:
        await websocket.close(REJECTION_CLOSE_CODES[rejection], close_reason(reason))


def create_router(
    service: RpcService,
    *,
    resolver: RpcResolverLike | None = None,
    resolver_factory: FastApiResolverFactory | None = None,
    context: object | Mapping[type[Any], object] | None = None,
    error_mapper: RpcErrorMapper | None = None,
    limits: RpcLimits | None = None,
    before_accept: RpcBeforeAccept | None = None,
    rejections: RpcRejections | None = None,
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
            rejections=rejections,
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
    rejections: RpcRejections | None,
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
            rejections=rejections,
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
    rejections: RpcRejections | None = None,
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
                rejections=rejections,
            )
        else:
            await endpoint.serve(
                socket,
                resolver=resolver,
                context=context,
                limits=limits,
                before_accept=before_accept,
                error_mapper=error_mapper,
                rejections=rejections,
            )
    except asyncio.CancelledError:
        # Starlette cancels its WebSocket task after websocket.disconnect.
        return


class RpcWebSockets:
    """Mount rpckit endpoints as WebSocket routes on an existing FastAPI router.

    Values provided through FastAPI dependencies reach handlers as ``Inject[T]``.
    Failures covered by ``rejections`` reject the handshake or close the socket,
    depending on whether it was already accepted.
    """

    def __init__(
        self,
        router: APIRouter,
        *,
        provide: Sequence[Any] = (),
        resolver: RpcResolverLike | None = None,
        resolver_factory: FastApiResolverFactory | None = None,
        rejections: RpcRejections | None = None,
        error_mapper: RpcErrorMapper | None = None,
        limits: RpcLimits | None = None,
    ) -> None:
        if resolver is not None and resolver_factory is not None:
            raise ValueError("resolver and resolver_factory are mutually exclusive")
        self._router = router
        self._provide = tuple(provide)
        self._resolver = resolver
        self._resolver_factory = resolver_factory
        self._rejections = rejections
        self._error_mapper = error_mapper
        self._limits = limits
        self._mounted: set[RpcEndpoint | RpcStreamEndpoint] = set()
        _provided_types(self._provide)

    def mount(
        self,
        endpoint: RpcEndpoint | RpcStreamEndpoint,
        *,
        provide: Sequence[Any] = (),
        rejections: RpcRejections | None = None,
    ) -> None:
        """Serve ``endpoint``, supplying each ``Annotated[T, Depends(...)]`` as T."""
        self._add(endpoint, (*self._provide, *provide), None, rejections)

    def context(
        self,
        endpoint: RpcEndpoint | RpcStreamEndpoint,
        *,
        rejections: RpcRejections | None = None,
    ) -> Callable[[RpcContextFunction], RpcContextFunction]:
        """Serve ``endpoint`` with the context returned by the decorated dependency.

        The function may return one object, keyed by its type, or a mapping from
        types to values.
        """

        def decorator(function: RpcContextFunction) -> RpcContextFunction:
            self._add(endpoint, self._provide, function, rejections)
            return function

        return decorator

    def _add(
        self,
        endpoint: RpcEndpoint | RpcStreamEndpoint,
        provide: tuple[Any, ...],
        context: RpcContextFunction | None,
        rejections: RpcRejections | None,
    ) -> None:
        if endpoint in self._mounted:
            raise ValueError(f"RPC endpoint {endpoint.name!r} is already mounted")
        rejections = rejections if rejections is not None else self._rejections
        self._router.add_api_websocket_route(
            self._route_path(endpoint),
            self._handler(endpoint, provide, context, rejections),
            name=endpoint.name,
            dependencies=[Depends(_reject_failures(rejections))],
        )
        self._mounted.add(endpoint)

    def _route_path(self, endpoint: RpcEndpoint | RpcStreamEndpoint) -> str:
        prefix = self._router.prefix
        path = endpoint.path
        if not path.startswith(prefix) or path[len(prefix) :][:1] not in ("", "/"):
            raise ValueError(
                f"RPC endpoint path {path!r} is outside the router prefix {prefix!r}"
            )
        return path[len(prefix) :]

    def _handler(
        self,
        endpoint: RpcEndpoint | RpcStreamEndpoint,
        provide: tuple[Any, ...],
        context: RpcContextFunction | None,
        rejections: RpcRejections | None,
    ) -> Callable[..., Awaitable[None]]:
        types = _provided_types(provide)

        async def handler(websocket: WebSocket, **values: Any) -> None:
            connection_context = {
                dependency: values[f"provided_{index}"]
                for index, dependency in enumerate(types)
            }
            if context is not None:
                connection_context.update(context_values(values["context"]))
            await serve_websocket(
                endpoint,
                websocket,
                resolver=(
                    self._resolver_factory(websocket)
                    if self._resolver_factory is not None
                    else self._resolver
                ),
                context=connection_context,
                error_mapper=self._error_mapper,
                limits=self._limits,
                rejections=rejections,
            )

        parameters = [
            inspect.Parameter(
                "websocket",
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                annotation=WebSocket,
            ),
            *(
                inspect.Parameter(
                    f"provided_{index}",
                    inspect.Parameter.KEYWORD_ONLY,
                    annotation=annotation,
                )
                for index, annotation in enumerate(provide)
            ),
        ]
        if context is not None:
            parameters.append(
                inspect.Parameter(
                    "context",
                    inspect.Parameter.KEYWORD_ONLY,
                    annotation=Annotated[Any, Depends(context)],
                )
            )
        handler.__signature__ = inspect.Signature(parameters)  # type: ignore[attr-defined]
        return handler


def _provided_types(provide: Sequence[Any]) -> tuple[type[Any], ...]:
    types = []
    for annotation in provide:
        dependency = (
            get_args(annotation)[0] if get_origin(annotation) is Annotated else None
        )
        if not inspect.isclass(dependency):
            raise TypeError(
                "provide= expects Annotated[T, Depends(...)] with a concrete type T, "
                f"got {annotation!r}"
            )
        if dependency in types:
            raise TypeError(f"{dependency.__name__} is provided more than once")
        types.append(dependency)
    return tuple(types)


def _reject_failures(
    rejections: RpcRejections | None,
) -> Callable[[WebSocket], AsyncIterator[None]]:
    # Registered as the route's first dependency, so its exit sees failures of
    # later dependencies and of the handler.
    async def reject_failures(websocket: WebSocket) -> AsyncIterator[None]:
        try:
            yield
        except Exception as error:
            rejected = _rejection(error, rejections)
            if rejected is None:
                raise
            if websocket.application_state is WebSocketState.CONNECTING:
                await _reject(
                    websocket,
                    rejected.rejection,
                    rejected.reason,
                    headers=rejected.headers,
                )
            elif websocket.application_state is WebSocketState.CONNECTED:
                await websocket.close(
                    CLOSE_CODES[REJECTION_CLOSES[rejected.rejection]],
                    close_reason(rejected.reason),
                )

    return reject_failures
