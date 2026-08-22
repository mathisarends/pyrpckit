from collections.abc import Callable

from pydantic import ValidationError

from pyrpckit.decorators import RpcHandler
from pyrpckit.dispatch import RpcDispatcher
from pyrpckit.envelopes import RpcFailure, RpcRequestId, RpcSuccess
from pyrpckit.errors import (
    RpcError,
    RpcInternalError,
    RpcInvalidParamsError,
    RpcInvalidRequestError,
)
from pyrpckit.protocol import RpcProtocol

type RpcErrorMapper = Callable[[Exception], RpcError | None]


class RpcServer:
    """Serves a protocol over any transport that can carry decoded JSON.

    The protocol is derived from the handler instances unless one is passed
    explicitly, in which case the two are checked against each other.

    Handlers report failures by raising an ``RpcError`` subclass, which is put on
    the wire as declared. Foreign exceptions are translated by ``error_mapper``;
    anything it does not recognise becomes an internal error, so handler
    internals never leak.
    """

    def __init__(
        self,
        *handlers: RpcHandler,
        protocol: RpcProtocol | None = None,
        error_mapper: RpcErrorMapper | None = None,
    ) -> None:
        self._protocol = (
            protocol if protocol is not None else _derived_protocol(handlers)
        )
        self._dispatcher = RpcDispatcher(self._protocol, handlers)
        self._error_mapper = error_mapper

    @property
    def protocol(self) -> RpcProtocol:
        return self._protocol

    async def handle(self, raw_request: object) -> RpcSuccess | RpcFailure | None:
        """Serve one request, returning ``None`` for a notification."""
        try:
            invocation = self._dispatcher.parse_request(raw_request)
            result = await self._dispatcher.execute(invocation)
        except Exception as error:
            return self.failure(_request_id(raw_request), error)
        if not invocation.request.expects_response:
            return None
        return RpcSuccess(id=invocation.request.id, result=result)

    def failure(self, request_id: RpcRequestId, error: Exception) -> RpcFailure:
        rpc_error = self._rpc_error(error)
        return RpcFailure.of(request_id, rpc_error.code, rpc_error.message)

    def _rpc_error(self, error: Exception) -> RpcError:
        if isinstance(error, RpcError):
            return error
        if self._error_mapper is not None:
            mapped = self._error_mapper(error)
            if mapped is not None:
                return mapped
        if isinstance(error, ValidationError):
            return _validation_error(error)
        return RpcInternalError()


def _derived_protocol(handlers: tuple[RpcHandler, ...]) -> RpcProtocol:
    return RpcProtocol.of(*(type(handler) for handler in handlers))


def _validation_error(error: ValidationError) -> RpcError:
    if any("params" in issue["loc"] for issue in error.errors(include_url=False)):
        return RpcInvalidParamsError(error)
    return RpcInvalidRequestError()


def _request_id(raw_request: object) -> RpcRequestId:
    if not isinstance(raw_request, dict):
        return None
    request_id = raw_request.get("id")
    if isinstance(request_id, bool):
        return None
    return request_id if isinstance(request_id, str | int) else None
