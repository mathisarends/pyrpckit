from pydantic import BaseModel

import pyrpckit as rpc
from pyrpckit import RpcError, RpcErrorCode, RpcFailure, RpcProtocol, RpcServer, RpcSuccess

from .conftest import GreetingRpcMethod, GreetingRpcMethods, SayParams, UnknownGreetingError


def _greeting_error(error: Exception) -> RpcError | None:
    if isinstance(error, UnknownGreetingError):
        return RpcError(-32004, f"Unknown greeting: {error}")
    return None


def _server(protocol: RpcProtocol, handler: GreetingRpcMethods) -> RpcServer:
    return RpcServer(protocol, (handler,), error_mapper=_greeting_error)


async def test_a_request_is_answered_with_its_result(
    protocol: RpcProtocol,
    handler: GreetingRpcMethods,
) -> None:
    response = await _server(protocol, handler).handle(
        {"jsonrpc": "2.0", "id": 7, "method": GreetingRpcMethod.SAY, "params": {"name": "M"}}
    )

    assert isinstance(response, RpcSuccess)
    assert response.id == 7
    assert response.result is not None
    assert response.result.text == "Hello, M!"


async def test_a_notification_is_served_without_a_response(
    protocol: RpcProtocol,
    handler: GreetingRpcMethods,
) -> None:
    response = await _server(protocol, handler).handle(
        {"jsonrpc": "2.0", "method": GreetingRpcMethod.SAY, "params": {"name": "M"}}
    )

    assert response is None
    assert handler.greeted == ["M"]


async def test_an_unknown_method_becomes_a_failure(
    protocol: RpcProtocol,
    handler: GreetingRpcMethods,
) -> None:
    response = await _server(protocol, handler).handle(
        {"jsonrpc": "2.0", "id": 1, "method": "greeting.unknown", "params": {}}
    )

    assert isinstance(response, RpcFailure)
    assert response.id == 1
    assert response.error.code == RpcErrorCode.METHOD_NOT_FOUND


async def test_invalid_params_become_a_failure(
    protocol: RpcProtocol,
    handler: GreetingRpcMethods,
) -> None:
    response = await _server(protocol, handler).handle(
        {"jsonrpc": "2.0", "id": 1, "method": GreetingRpcMethod.SAY, "params": {}}
    )

    assert isinstance(response, RpcFailure)
    assert response.error.code == RpcErrorCode.INVALID_PARAMS
    assert "params.name" in response.error.message


async def test_a_malformed_envelope_becomes_an_invalid_request(
    protocol: RpcProtocol,
    handler: GreetingRpcMethods,
) -> None:
    response = await _server(protocol, handler).handle({"id": 1, "method": "x"})

    assert isinstance(response, RpcFailure)
    assert response.error.code == RpcErrorCode.INVALID_REQUEST


async def test_domain_errors_are_translated_by_the_error_mapper(
    protocol: RpcProtocol,
    handler: GreetingRpcMethods,
) -> None:
    response = await _server(protocol, handler).handle(
        {"jsonrpc": "2.0", "id": 1, "method": GreetingRpcMethod.FORGET, "params": {"name": "M"}}
    )

    assert isinstance(response, RpcFailure)
    assert response.error.code == -32004
    assert response.error.message == "Unknown greeting: M"


async def test_unmapped_handler_failures_stay_internal(
    protocol: RpcProtocol,
    handler: GreetingRpcMethods,
) -> None:
    server = RpcServer(protocol, (handler,))

    response = await server.handle(
        {"jsonrpc": "2.0", "id": 1, "method": GreetingRpcMethod.FORGET, "params": {"name": "M"}}
    )

    assert isinstance(response, RpcFailure)
    assert response.error.code == RpcErrorCode.INTERNAL_ERROR
    assert response.error.message == "Internal error"


async def test_a_non_object_payload_fails_without_an_id(
    protocol: RpcProtocol,
    handler: GreetingRpcMethods,
) -> None:
    response = await _server(protocol, handler).handle("nonsense")

    assert isinstance(response, RpcFailure)
    assert response.id is None


def test_rpc_errors_carry_their_own_code(
    protocol: RpcProtocol,
    handler: GreetingRpcMethods,
) -> None:
    failure = _server(protocol, handler).failure(3, rpc.RpcError(-32001, "Busy"))

    assert failure.id == 3
    assert failure.error.code == -32001


async def test_a_boolean_id_on_a_failed_request_is_not_echoed_back(
    protocol: RpcProtocol,
    handler: GreetingRpcMethods,
) -> None:
    response = await _server(protocol, handler).handle(
        {"jsonrpc": "2.0", "id": True, "method": "greeting.unknown", "params": {}}
    )

    assert isinstance(response, RpcFailure)
    assert response.id is None


async def test_a_validation_error_naming_a_params_field_becomes_invalid_params() -> None:
    class NestedParams(BaseModel):
        params: str

    class Handler(rpc.RpcHandler):
        @rpc.method("greeting.broken", summary="Trigger an internal validation error.")
        async def broken(self, params: SayParams) -> None:
            NestedParams.model_validate({"params": 1})

    feature = rpc.rpc_feature("greeting", handlers=(Handler,))
    broken_handler = Handler()
    server = RpcServer(RpcProtocol((feature,)), (broken_handler,))

    response = await server.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "greeting.broken", "params": {"name": "M"}}
    )

    assert isinstance(response, RpcFailure)
    assert response.error.code == RpcErrorCode.INVALID_PARAMS
