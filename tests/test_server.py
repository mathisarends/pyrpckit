from pydantic import BaseModel

import pyrpckit as rpc
from pyrpckit import (
    RpcError,
    RpcErrorCode,
    RpcFailure,
    RpcServer,
    RpcSuccess,
)

from .conftest import GREETING_APP, GreetingRpcMethod, GreetingRpcMethods, SayParams


def _server(handler: GreetingRpcMethods) -> RpcServer:
    return GREETING_APP.bind(handler)


async def test_a_request_is_answered_with_its_result(
    handler: GreetingRpcMethods,
) -> None:
    response = await _server(handler).handle(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": GreetingRpcMethod.SAY,
            "params": {"name": "M"},
        }
    )

    assert isinstance(response, RpcSuccess)
    assert response.id == 7
    assert response.result is not None
    assert response.result.text == "Hello, M!"


async def test_a_notification_is_served_without_a_response(
    handler: GreetingRpcMethods,
) -> None:
    response = await _server(handler).handle(
        {"jsonrpc": "2.0", "method": GreetingRpcMethod.SAY, "params": {"name": "M"}}
    )

    assert response is None
    assert handler.greeted == ["M"]


async def test_an_unknown_method_becomes_a_failure(handler: GreetingRpcMethods) -> None:
    response = await _server(handler).handle(
        {"jsonrpc": "2.0", "id": 1, "method": "greeting.unknown", "params": {}}
    )

    assert isinstance(response, RpcFailure)
    assert response.id == 1
    assert response.error.code == RpcErrorCode.METHOD_NOT_FOUND


async def test_invalid_params_become_a_failure(handler: GreetingRpcMethods) -> None:
    response = await _server(handler).handle(
        {"jsonrpc": "2.0", "id": 1, "method": GreetingRpcMethod.SAY, "params": {}}
    )

    assert isinstance(response, RpcFailure)
    assert response.error.code == RpcErrorCode.INVALID_PARAMS
    assert "params.name" in response.error.message


async def test_a_malformed_envelope_becomes_an_invalid_request(
    handler: GreetingRpcMethods,
) -> None:
    response = await _server(handler).handle({"id": 1, "method": "x"})

    assert isinstance(response, RpcFailure)
    assert response.error.code == RpcErrorCode.INVALID_REQUEST


async def test_a_declared_error_goes_on_the_wire_as_declared(
    handler: GreetingRpcMethods,
) -> None:
    response = await _server(handler).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": GreetingRpcMethod.FORGET,
            "params": {"name": "M"},
        }
    )

    assert isinstance(response, RpcFailure)
    assert response.error.code == -32001
    assert response.error.message == "Unknown greeting: M"


async def test_foreign_errors_are_translated_by_the_error_mapper() -> None:
    server = BROKEN_APP.bind(BrokenRpcMethods(), error_mapper=_broken_error)

    response = await server.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "greeting.break"}
    )

    assert isinstance(response, RpcFailure)
    assert response.error.code == -32004
    assert response.error.message == "Mapped: boom"


async def test_unmapped_handler_failures_stay_internal() -> None:
    server = BROKEN_APP.bind(BrokenRpcMethods())

    response = await server.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "greeting.break"}
    )

    assert isinstance(response, RpcFailure)
    assert response.error.code == RpcErrorCode.INTERNAL_ERROR
    assert response.error.message == "Internal error"


async def test_a_non_object_payload_fails_without_an_id(
    handler: GreetingRpcMethods,
) -> None:
    response = await _server(handler).handle("nonsense")

    assert isinstance(response, RpcFailure)
    assert response.id is None


def test_rpc_errors_carry_their_own_code(handler: GreetingRpcMethods) -> None:
    failure = _server(handler).failure(3, RpcError("Busy", code=-32001))

    assert failure.id == 3
    assert failure.error.code == -32001


async def test_a_boolean_id_on_a_failed_request_is_not_echoed_back(
    handler: GreetingRpcMethods,
) -> None:
    response = await _server(handler).handle(
        {"jsonrpc": "2.0", "id": True, "method": "greeting.unknown", "params": {}}
    )

    assert isinstance(response, RpcFailure)
    assert response.id is None


async def test_a_validation_error_naming_a_params_field_becomes_invalid_params() -> (
    None
):
    class NestedParams(BaseModel):
        params: str

    router = rpc.RpcRouter(namespace="greeting")

    class Handler:
        @router.method("broken")
        async def broken(self, params: SayParams) -> None:
            NestedParams.model_validate({"params": 1})

    app = rpc.RpcApp()
    app.include_router(router)
    response = await app.bind(Handler()).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "greeting.broken",
            "params": {"name": "M"},
        }
    )

    assert isinstance(response, RpcFailure)
    assert response.error.code == RpcErrorCode.INVALID_PARAMS


class BreakageError(Exception):
    pass


class BrokenParams(BaseModel):
    pass


BROKEN_ROUTER = rpc.RpcRouter(namespace="greeting")


class BrokenRpcMethods:
    @BROKEN_ROUTER.method("break")
    async def fail(self, params: BrokenParams) -> None:
        raise BreakageError("boom")


BROKEN_APP = rpc.RpcApp()
BROKEN_APP.include_router(BROKEN_ROUTER)


def _broken_error(error: Exception) -> RpcError | None:
    if isinstance(error, BreakageError):
        return RpcError(f"Mapped: {error}", code=-32004)
    return None
