from typing import Annotated

import pytest
from pydantic import BaseModel

import pyrpckit as rpc
from pyrpckit import ProtocolDefinitionError, RpcProtocol, rpc_feature
from pyrpckit.protocol import RpcNotificationDefinition

from .conftest import (
    GREETING_FEATURE,
    ForgetParams,
    GreetingForgotten,
    GreetingNotificationMethod,
    GreetingRpcMethod,
    GreetingSaid,
    SayParams,
    SayResult,
)


def test_the_protocol_collects_methods_from_its_features(
    protocol: RpcProtocol,
) -> None:
    assert [method.name for method in protocol.methods] == [
        GreetingRpcMethod.SAY,
        GreetingRpcMethod.FORGET,
    ]


def test_the_protocol_exposes_the_features_it_was_built_from(
    protocol: RpcProtocol,
) -> None:
    assert protocol.features == (GREETING_FEATURE,)


def test_a_method_definition_describes_its_wire_contract(
    protocol: RpcProtocol,
) -> None:
    say = protocol.method(GreetingRpcMethod.SAY)

    assert say.handler_name == "say"
    assert say.request_name == "SayRequest"
    assert say.params is SayParams
    assert say.result is SayResult


def test_a_method_may_return_nothing(protocol: RpcProtocol) -> None:
    forget = protocol.method(GreetingRpcMethod.FORGET)

    assert forget.params is ForgetParams
    assert forget.result is type(None)


def test_notifications_expand_into_their_union_of_events(
    protocol: RpcProtocol,
) -> None:
    assert [notification.name for notification in protocol.notifications] == [
        GreetingNotificationMethod.CHANGED
    ]
    assert [event.name for event in protocol.events] == [
        "greeting.said",
        "greeting.forgotten",
    ]


def test_an_unknown_method_is_rejected(protocol: RpcProtocol) -> None:
    with pytest.raises(rpc.RpcMethodNotFoundError):
        protocol.method("greeting.unknown")


def test_features_may_not_declare_the_same_method_twice() -> None:
    with pytest.raises(ProtocolDefinitionError, match="Duplicate RPC method"):
        RpcProtocol((GREETING_FEATURE, GREETING_FEATURE))


def test_params_must_be_a_pydantic_model() -> None:
    class Handler(rpc.RpcHandler):
        @rpc.method("greeting.say", summary="Greet someone.")
        async def say(self, params: str) -> None: ...

    with pytest.raises(ProtocolDefinitionError, match="params must be"):
        rpc_feature("greeting", handlers=(Handler,))


def test_a_result_annotation_is_required() -> None:
    class Handler(rpc.RpcHandler):
        @rpc.method("greeting.say", summary="Greet someone.")
        async def say(self, params: SayParams): ...

    with pytest.raises(ProtocolDefinitionError, match="needs a return annotation"):
        rpc_feature("greeting", handlers=(Handler,))


def test_a_handler_takes_exactly_self_and_params() -> None:
    class Handler(rpc.RpcHandler):
        @rpc.method("greeting.say", summary="Greet someone.")
        async def say(self, params: SayParams, extra: int) -> None: ...

    with pytest.raises(ProtocolDefinitionError, match="only self and params"):
        rpc_feature("greeting", handlers=(Handler,))


def test_notification_payloads_must_be_decorated_events() -> None:
    class Undecorated(BaseModel):
        text: str

    notification = RpcNotificationDefinition(
        name="greeting.changed",
        payload=Undecorated,
        summary="Publish a greeting change.",
    )

    with pytest.raises(ProtocolDefinitionError, match="not decorated"):
        rpc_feature("greeting", handlers=(), notifications=(notification,))


def test_a_result_must_be_a_pydantic_model_or_none() -> None:
    class Handler(rpc.RpcHandler):
        @rpc.method("greeting.say", summary="Greet someone.")
        async def say(self, params: SayParams) -> str: ...

    with pytest.raises(ProtocolDefinitionError, match="result must be"):
        rpc_feature("greeting", handlers=(Handler,))


def test_notification_payloads_must_be_pydantic_models() -> None:
    notification = RpcNotificationDefinition(
        name="greeting.changed",
        payload=str,
        summary="Publish a greeting change.",
    )

    with pytest.raises(ProtocolDefinitionError, match="must contain Pydantic event models"):
        rpc_feature("greeting", handlers=(), notifications=(notification,))


def test_an_annotated_union_of_events_still_expands_into_events() -> None:
    notification = RpcNotificationDefinition(
        name="greeting.changed",
        payload=Annotated[GreetingSaid | GreetingForgotten, "wire payload"],
        summary="Publish a greeting change.",
    )

    feature = rpc_feature("greeting", handlers=(), notifications=(notification,))

    assert [event.name for event in feature.events] == [
        "greeting.said",
        "greeting.forgotten",
    ]
