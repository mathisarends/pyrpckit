import asyncio
import inspect
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from typing import (
    Annotated,
    Any,
    Protocol,
    get_origin,
    get_type_hints,
    runtime_checkable,
)

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
from rpckit.dependencies import RpcResolverLike
from rpckit.runtime import _rejection
from rpckit.server import RpcErrorMapper
from rpckit.service import RpcEndpoint, RpcService, RpcStreamEndpoint
from rpckit.websocket import CLOSE_CODES, REJECTION_CLOSE_CODES, close_reason

type FastApiResolverFactory = Callable[[WebSocket], RpcResolverLike]
type RpcProvider = Callable[..., Any]

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


@runtime_checkable
class FastApiResolver(Protocol):
    """Resolve RPC dependencies per WebSocket and prepare ``provide=`` functions.

    Pass an implementation, such as ``rpckit.dishka.Dishka``, as ``resolver=``
    to ``RpcWebSockets`` to integrate a DI library.
    """

    def for_websocket(self, websocket: WebSocket) -> RpcResolverLike: ...

    def dependency[FunctionT: Callable[..., Any]](
        self, function: FunctionT
    ) -> FunctionT: ...


class RpcWebSockets:
    """Mount rpckit endpoints as WebSocket routes on an existing FastAPI router.

    Each function in ``provide=`` runs as a FastAPI dependency per connection,
    path parameters included, and its result reaches handlers as
    ``Inject[T]``, where T is the function's return annotation. Failures
    covered by ``rejections`` reject the handshake or close the socket,
    depending on whether it was already accepted.
    """

    def __init__(
        self,
        router: APIRouter,
        *,
        resolver: RpcResolverLike | FastApiResolver | None = None,
        provide: Sequence[RpcProvider] = (),
        rejections: RpcRejections | None = None,
        error_mapper: RpcErrorMapper | None = None,
        limits: RpcLimits | None = None,
    ) -> None:
        self._router = router
        self._resolver: RpcResolverLike | None = None
        self._websocket_resolver: FastApiResolver | None = None
        if isinstance(resolver, FastApiResolver):
            self._websocket_resolver = resolver
        else:
            self._resolver = resolver
        self._provide = _providers(provide)
        self._rejections = rejections
        self._error_mapper = error_mapper
        self._limits = limits
        self._mounted: set[RpcEndpoint | RpcStreamEndpoint] = set()

    def mount(
        self,
        *endpoints: RpcEndpoint | RpcStreamEndpoint,
        provide: Sequence[RpcProvider] = (),
        rejections: RpcRejections | None = None,
    ) -> None:
        """Add a WebSocket route for each endpoint at its declared path."""
        if not endpoints:
            raise TypeError("mount() needs at least one endpoint")
        providers = {**self._provide}
        for dependency, function in _providers(provide).items():
            if dependency in providers:
                raise TypeError(f"{dependency.__name__} is provided more than once")
            providers[dependency] = function
        rejections = rejections if rejections is not None else self._rejections
        for endpoint in endpoints:
            if endpoint in self._mounted:
                raise ValueError(f"RPC endpoint {endpoint.name!r} is already mounted")
            self._router.add_api_websocket_route(
                self._route_path(endpoint),
                self._handler(endpoint, providers, rejections),
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
        providers: Mapping[type[Any], RpcProvider],
        rejections: RpcRejections | None,
    ) -> Callable[..., Awaitable[None]]:
        types = tuple(providers)

        async def handler(websocket: WebSocket, **values: Any) -> None:
            await serve_websocket(
                endpoint,
                websocket,
                resolver=(
                    self._websocket_resolver.for_websocket(websocket)
                    if self._websocket_resolver is not None
                    else self._resolver
                ),
                context={
                    dependency: values[f"provided_{index}"]
                    for index, dependency in enumerate(types)
                },
                error_mapper=self._error_mapper,
                limits=self._limits,
                rejections=rejections,
            )

        handler.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
            [
                inspect.Parameter(
                    "websocket",
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    annotation=WebSocket,
                ),
                *(
                    inspect.Parameter(
                        f"provided_{index}",
                        inspect.Parameter.KEYWORD_ONLY,
                        annotation=Annotated[Any, Depends(self._dependency(function))],
                    )
                    for index, function in enumerate(providers.values())
                ),
            ]
        )
        return handler

    def _dependency(self, function: RpcProvider) -> RpcProvider:
        if self._websocket_resolver is not None:
            return self._websocket_resolver.dependency(function)
        return function


def _providers(provide: Sequence[RpcProvider]) -> dict[type[Any], RpcProvider]:
    providers: dict[type[Any], RpcProvider] = {}
    for function in provide:
        dependency = _provided_type(function)
        if dependency in providers:
            raise TypeError(f"{dependency.__name__} is provided more than once")
        providers[dependency] = function
    return providers


def _provided_type(function: RpcProvider) -> type[Any]:
    if (
        not callable(function)
        or inspect.isclass(function)
        or get_origin(function) is not None
    ):
        raise TypeError(f"provide= expects functions, got {function!r}")
    dependency = get_type_hints(function).get("return")
    if dependency in (Any, object, type(None)) or not inspect.isclass(dependency):
        raise TypeError(
            f"{getattr(function, '__qualname__', function)!r} in provide= needs a "
            f"concrete class as return annotation, got {dependency!r}"
        )
    return dependency


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
