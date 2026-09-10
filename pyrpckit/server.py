from collections.abc import Callable

from pydantic import ValidationError

from pyrpckit.codec import RpcCodec
from pyrpckit.dependencies import RpcResolver
from pyrpckit.dispatch import RpcDispatcher
from pyrpckit.envelopes import RpcFailure, RpcRequestId, RpcSuccess
from pyrpckit.errors import (
    RpcError,
    RpcInternalError,
    RpcInvalidParamsError,
    RpcInvalidRequestError,
    RpcParseError,
)
from pyrpckit.protocol import RpcProtocol

type RpcErrorMapper = Callable[[Exception], RpcError | None]
type RpcResponse = RpcSuccess | RpcFailure
type RpcResponseMessage = RpcResponse | list[RpcResponse] | None


class RpcServer:
    """Serve decoded or encoded JSON-RPC messages over any transport."""

    def __init__(self) -> None:
        raise TypeError("RpcServer instances are created by RpcChannel.server()")

    @classmethod
    def _from_channel(
        cls,
        protocol: RpcProtocol,
        *,
        resolver: RpcResolver | None = None,
        error_mapper: RpcErrorMapper | None = None,
    ) -> "RpcServer":
        server = cls.__new__(cls)
        server._protocol = protocol
        server._dispatcher = RpcDispatcher(protocol, resolver=resolver)
        server._error_mapper = error_mapper
        server._codec = RpcCodec()
        return server

    @property
    def protocol(self) -> RpcProtocol:
        return self._protocol

    async def handle(self, raw_request: object) -> RpcResponseMessage:
        """Serve one decoded request or batch."""
        if isinstance(raw_request, list):
            if not raw_request:
                return self.failure(None, RpcInvalidRequestError())
            responses = [await self._handle_one(item) for item in raw_request]
            return [response for response in responses if response is not None] or None
        return await self._handle_one(raw_request)

    async def handle_json(self, message: str | bytes | bytearray) -> str | None:
        """Decode, serve, and encode one JSON-RPC message."""
        try:
            request = self._codec.decode(message)
        except RpcParseError as error:
            return self._codec.encode(self.failure(None, error))
        response = await self.handle(request)
        return None if response is None else self._codec.encode(response)

    async def _handle_one(self, raw_request: object) -> RpcResponse | None:
        try:
            invocation = self._dispatcher.parse_request(raw_request)
            result = await self._dispatcher.execute(invocation)
        except Exception as error:
            if _looks_like_notification(raw_request):
                return None
            return self.failure(_request_id(raw_request), error)
        if not invocation.request.expects_response:
            return None
        return RpcSuccess._with_result_annotation(
            invocation.request.id,
            result,
            invocation.method.result,
        )

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


def _looks_like_notification(raw_request: object) -> bool:
    return (
        isinstance(raw_request, dict)
        and "id" not in raw_request
        and raw_request.get("jsonrpc") == "2.0"
        and isinstance(raw_request.get("method"), str)
    )
