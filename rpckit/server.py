import asyncio
import logging
import time
from collections.abc import AsyncGenerator, Callable, Sequence
from contextlib import asynccontextmanager, nullcontext
from typing import TYPE_CHECKING, Any

from rpckit.codec import RpcCodec
from rpckit.connection import RpcConnection, RpcLimits
from rpckit.constants import LOGGER_NAME
from rpckit.dependencies import RpcResolver
from rpckit.dispatch import RpcDispatcher
from rpckit.envelopes import RpcFailure, RpcRequestId, RpcSuccess
from rpckit.errors import (
    RpcError,
    RpcInternalError,
    RpcInvalidRequestError,
    RpcParseError,
    bind_contracts,
    contract_for,
    contract_of,
)
from rpckit.middleware import (
    RpcMiddlewareLike,
    RpcRequestOutcome,
    RpcRequestScope,
    _middleware_observer,
    _middleware_scope,
)
from rpckit.observer import (
    RpcObserverLike,
    RpcRequestContext,
    RpcResponseContext,
    notify_observer,
)
from rpckit.protocol import RpcMethodDefinition, RpcProtocol

if TYPE_CHECKING:
    from rpckit.service import RpcEndpoint

type RpcErrorMapper = Callable[[Exception], RpcError | None]
type RpcResponse = RpcSuccess | RpcFailure
type RpcResponseMessage = RpcResponse | list[RpcResponse] | None

logger = logging.getLogger(LOGGER_NAME)


class RpcServer:
    """Serve decoded or encoded JSON-RPC messages over any transport."""

    def __init__(self) -> None:
        raise TypeError("RpcServer instances are created by RpcChannel.create_server()")

    @classmethod
    def _from_channel(
        cls,
        protocol: RpcProtocol,
        *,
        resolver: RpcResolver | None = None,
        error_mapper: RpcErrorMapper | None = None,
        observer: RpcObserverLike | None = None,
        connection: RpcConnection | None = None,
        limits: RpcLimits | None = None,
        strict_errors: bool = False,
        middleware: Sequence[RpcMiddlewareLike] = (),
        endpoint: "RpcEndpoint[Any] | None" = None,
    ) -> "RpcServer":
        server = cls.__new__(cls)
        server._protocol = protocol
        server._dispatcher = RpcDispatcher(protocol, resolver=resolver)
        server._error_mapper = error_mapper
        server._middleware = tuple(middleware)
        server._observer = _middleware_observer(observer, middleware)
        server._endpoint = endpoint
        server._connection = connection
        server._codec = RpcCodec()
        server._limits = limits or RpcLimits()
        server._semaphore = asyncio.Semaphore(server._limits.max_concurrency)
        server._strict_errors = strict_errors
        server._observed_methods = (
            {method.name: method for method in protocol.methods}
            if server._middleware
            else {}
        )
        for subscription in protocol.subscriptions if server._middleware else ():
            for suffix in ("subscribe", "unsubscribe"):
                name = f"{subscription.name}.{suffix}"
                server._observed_methods[name] = RpcMethodDefinition(
                    name=name,
                    handler_name=name,
                    request_name=name,
                    params=subscription.params if suffix == "subscribe" else None,
                    result=Any,
                    raises=subscription.raises,
                )
        return server

    @property
    def protocol(self) -> RpcProtocol:
        return self._protocol

    async def handle(self, raw_request: object) -> RpcResponseMessage:
        """Serve one decoded request or batch."""
        if isinstance(raw_request, list):
            if not raw_request or len(raw_request) > self._limits.max_batch_size:
                return await self._handle_failure(raw_request, RpcInvalidRequestError())
            responses = await asyncio.gather(
                *(self._handle_one_bounded(item) for item in raw_request)
            )
            return [response for response in responses if response is not None] or None
        return await self._handle_one_bounded(raw_request)

    async def _handle_one_bounded(self, raw_request: object) -> RpcResponse | None:
        async with self._semaphore:
            return await self._handle_one(raw_request)

    async def handle_json(self, message: str | bytes | bytearray) -> str | None:
        """Decode, serve, and encode one JSON-RPC message."""
        try:
            request = self._codec.decode(message)
        except RpcParseError as error:
            return self._codec.encode(await self._handle_failure(message, error))
        response = await self.handle(request)
        return None if response is None else self._codec.encode(response)

    @asynccontextmanager
    async def _observe_request(
        self, raw_request: object
    ) -> AsyncGenerator[RpcRequestScope, None]:
        request = RpcRequestContext(
            raw_request=raw_request,
            method=_request_method(raw_request),
            request_id=_request_id(raw_request),
            notification=_looks_like_notification(raw_request),
            connection=self._connection,
        )
        scope = RpcRequestScope(
            request,
            self._endpoint,
            self._observed_methods.get(request.method or ""),
        )
        started = time.perf_counter()
        caught: BaseException | None = None
        async with (
            _middleware_scope(self._middleware, "request", scope)
            if self._middleware
            else nullcontext()
        ):
            try:
                await notify_observer(self._observer, "request_started", request)
                yield scope
            except BaseException as error:
                caught = error
                raise
            finally:
                previous = scope.outcome
                error = caught if previous is None else previous.error or caught
                scope.outcome = RpcRequestOutcome(
                    response=None if previous is None else previous.response,
                    duration=time.perf_counter() - started,
                    error=error,
                    cancelled=isinstance(caught, asyncio.CancelledError),
                    error_code=None if previous is None else previous.error_code,
                )
                await notify_observer(
                    self._observer,
                    "request_finished",
                    RpcResponseContext(
                        request, scope.outcome.response, scope.outcome.duration, error
                    ),
                )

    async def _handle_one(
        self, raw_request: object, *, failure: Exception | None = None
    ) -> RpcResponse | None:
        if self._observer is None:
            return await self._dispatch_request(raw_request, failure=failure)
        async with self._observe_request(raw_request) as scope:
            return await self._dispatch_request(
                raw_request, failure=failure, scope=scope
            )

    async def _dispatch_request(
        self,
        raw_request: object,
        *,
        failure: Exception | None = None,
        scope: RpcRequestScope | None = None,
    ) -> RpcResponse | None:
        invocation = None
        response: RpcResponse | None = None
        try:
            if failure is not None:
                raise failure
            invocation = self._dispatcher.parse_request(raw_request)
            result = await self._dispatcher.execute(invocation)
        except Exception as error:
            mapped = self._rpc_error(
                error,
                _request_method(raw_request) if scope is None else scope.request.method,
                declared=invocation.method.raises if invocation else (),
            )
            notification = (
                _looks_like_notification(raw_request)
                if scope is None
                else scope.request.notification
            )
            if not notification:
                response = RpcFailure.from_error(
                    _request_id(raw_request)
                    if scope is None
                    else scope.request.request_id,
                    mapped,
                )
            if scope is not None:
                scope.outcome = RpcRequestOutcome(
                    response, 0.0, error, error_code=str(mapped.rpc_code)
                )
        else:
            response = (
                RpcSuccess._with_result_annotation(
                    invocation.request.id, result, invocation.method.result
                )
                if invocation.request.expects_response
                else None
            )
            if scope is not None:
                scope.outcome = RpcRequestOutcome(response, 0.0)
        return response

    async def _handle_failure(
        self, raw_request: object, error: Exception
    ) -> RpcResponse | None:
        return await self._handle_one(raw_request, failure=error)

    def failure(
        self,
        request_id: RpcRequestId,
        error: Exception,
        *,
        method: str | None = None,
        declared: tuple[type[RpcError], ...] = (),
    ) -> RpcFailure:
        rpc_error = self._rpc_error(error, method, declared=declared)
        return RpcFailure.from_error(request_id, rpc_error)

    def _rpc_error(
        self,
        error: Exception,
        method: str | None = None,
        *,
        declared: tuple[type[RpcError], ...] = (),
    ) -> RpcError:
        if isinstance(error, RpcError):
            mapped = error
        elif (
            contract := contract_for(
                error,
                bind_contracts(
                    contract
                    for declared_error in declared
                    if (contract := contract_of(declared_error)) is not None
                ),
            )
        ) is not None:
            try:
                mapped = contract.to_error(error)
            except Exception:
                logger.exception(
                    "RPC error binding %s failed for method %s", contract.code, method
                )
                return RpcInternalError()
        else:
            try:
                mapped = (
                    self._error_mapper(error)
                    if self._error_mapper is not None
                    else None
                )
                if mapped is not None and not isinstance(mapped, RpcError):
                    raise TypeError("RPC error mapper must return RpcError or None")
            except Exception:
                logger.exception("RPC error mapper failed for method %s", method)
                return RpcInternalError()
        if mapped is not None:
            if (
                self._strict_errors
                and method is not None
                and not mapped._builtin
                and not isinstance(mapped, declared)
            ):
                logger.error(
                    "RPC method %s raised undeclared error %s",
                    method,
                    type(mapped).__name__,
                )
                return RpcInternalError()
            return mapped
        logger.error("RPC method %s failed", method, exc_info=error)
        return RpcInternalError()


def _request_id(raw_request: object) -> RpcRequestId:
    if not isinstance(raw_request, dict):
        return None
    request_id = raw_request.get("id")
    if isinstance(request_id, bool):
        return None
    return request_id if isinstance(request_id, str | int) else None


def _request_method(raw_request: object) -> str | None:
    if not isinstance(raw_request, dict):
        return None
    method = raw_request.get("method")
    return method if isinstance(method, str) else None


def _looks_like_notification(raw_request: object) -> bool:
    return (
        isinstance(raw_request, dict)
        and "id" not in raw_request
        and raw_request.get("jsonrpc") == "2.0"
        and isinstance(raw_request.get("method"), str)
    )
