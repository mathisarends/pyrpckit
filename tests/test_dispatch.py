import pytest

import pyrpckit as rpc
from pyrpckit import ProtocolDefinitionError, RpcProtocol
from pyrpckit.dispatch import RpcDispatcher

from .conftest import (
    GreetedResult,
    GreetingRpcMethod,
    GreetingRpcMethods,
    SayParams,
    SayResult,
)


def _request(name: str, params: dict[str, object]) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": 1, "method": name, "params": params}


def test_parsing_resolves_the_method_and_validates_the_params(
    protocol: RpcProtocol,
    handler: GreetingRpcMethods,
) -> None:
    dispatcher = RpcDispatcher(protocol, (handler,))

    invocation = dispatcher.parse_request(
        _request(GreetingRpcMethod.SAY, {"name": "Mathis"})
    )

    assert invocation.method.handler_name == "say"
    assert invocation.params == SayParams(name="Mathis")
    assert invocation.request.expects_response


async def test_execution_invokes_the_bound_handler(
    protocol: RpcProtocol,
    handler: GreetingRpcMethods,
) -> None:
    dispatcher = RpcDispatcher(protocol, (handler,))
    invocation = dispatcher.parse_request(
        _request(GreetingRpcMethod.SAY, {"name": "Mathis"})
    )

    result = await dispatcher.execute(invocation)

    assert result == SayResult(text="Hello, Mathis!")
    assert handler.greeted == ["Mathis"]


def test_a_request_without_an_id_is_a_notification(
    protocol: RpcProtocol,
    handler: GreetingRpcMethods,
) -> None:
    dispatcher = RpcDispatcher(protocol, (handler,))

    invocation = dispatcher.parse_request(
        {"jsonrpc": "2.0", "method": GreetingRpcMethod.SAY, "params": {"name": "M"}}
    )

    assert not invocation.request.expects_response


def test_unknown_methods_are_rejected(
    protocol: RpcProtocol,
    handler: GreetingRpcMethods,
) -> None:
    dispatcher = RpcDispatcher(protocol, (handler,))

    with pytest.raises(rpc.RpcMethodNotFoundError):
        dispatcher.parse_request(_request("greeting.unknown", {}))


def test_invalid_params_name_the_offending_field(
    protocol: RpcProtocol,
    handler: GreetingRpcMethods,
) -> None:
    dispatcher = RpcDispatcher(protocol, (handler,))

    with pytest.raises(rpc.RpcInvalidParamsError) as error:
        dispatcher.parse_request(_request(GreetingRpcMethod.SAY, {}))

    assert "params.name" in error.value.message
    assert error.value.code == rpc.RpcErrorCode.INVALID_PARAMS


def test_handlers_must_cover_the_whole_protocol(protocol: RpcProtocol) -> None:
    with pytest.raises(ProtocolDefinitionError, match="missing="):
        RpcDispatcher(protocol, ())


def test_two_handlers_may_not_serve_the_same_method(protocol: RpcProtocol) -> None:
    with pytest.raises(ProtocolDefinitionError, match="Duplicate RPC handler"):
        RpcDispatcher(protocol, (GreetingRpcMethods(), GreetingRpcMethods()))


async def test_a_method_without_params_is_invoked_without_them(
    protocol: RpcProtocol,
    handler: GreetingRpcMethods,
) -> None:
    dispatcher = RpcDispatcher(protocol, (handler,))
    invocation = dispatcher.parse_request(
        {"jsonrpc": "2.0", "id": 1, "method": GreetingRpcMethod.GREETED}
    )

    assert invocation.params is None
    assert await dispatcher.execute(invocation) == GreetedResult(names=[])


async def test_an_empty_params_member_still_reaches_a_method_without_params(
    protocol: RpcProtocol,
    handler: GreetingRpcMethods,
) -> None:
    dispatcher = RpcDispatcher(protocol, (handler,))

    invocation = dispatcher.parse_request(_request(GreetingRpcMethod.CLEAR, {}))

    assert invocation.params is None
    assert await dispatcher.execute(invocation) is None


def test_params_sent_to_a_method_without_params_are_rejected(
    protocol: RpcProtocol,
    handler: GreetingRpcMethods,
) -> None:
    dispatcher = RpcDispatcher(protocol, (handler,))

    with pytest.raises(rpc.RpcInvalidParamsError) as error:
        dispatcher.parse_request(_request(GreetingRpcMethod.CLEAR, {"name": "Mathis"}))

    assert "params.name" in error.value.message
    assert error.value.code == rpc.RpcErrorCode.INVALID_PARAMS
