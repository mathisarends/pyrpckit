from typing import Annotated

import pytest
from pydantic import BaseModel

import pyrpckit as rpc
from pyrpckit import ProtocolDefinitionError, RpcProtocol

from .conftest import (
    GREETING_FEATURE,
    ForgetParams,
    GreetingForgotten,
    GreetingNotificationMethod,
    GreetingRpcMethod,
    GreetingRpcMethods,
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


def test_a_method_carries_the_name_of_its_feature(protocol: RpcProtocol) -> None:
    assert protocol.method(GreetingRpcMethod.SAY).feature == "greeting"


def test_a_method_may_return_nothing(protocol: RpcProtocol) -> None:
    forget = protocol.method(GreetingRpcMethod.FORGET)

    assert forget.params is ForgetParams
    assert forget.result is type(None)


def test_a_protocol_can_be_assembled_from_handlers_alone() -> None:
    protocol = RpcProtocol.of(GreetingRpcMethods)

    assert [method.name for method in protocol.methods] == [
        GreetingRpcMethod.SAY,
        GreetingRpcMethod.FORGET,
    ]
    assert protocol.method(GreetingRpcMethod.SAY).feature is None


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
        RpcProtocol(GREETING_FEATURE, GREETING_FEATURE)


def test_a_handler_without_any_decorated_method_is_rejected() -> None:
    class Handler:
        async def helper(self, params: SayParams) -> None: ...

    with pytest.raises(ProtocolDefinitionError, match="declares no @method"):
        rpc.feature("greeting", handlers=(Handler,))


def test_a_handler_must_be_a_class() -> None:
    with pytest.raises(ProtocolDefinitionError, match="must be a class"):
        rpc.feature("greeting", handlers=(GreetingRpcMethods(),))


def test_params_must_be_a_pydantic_model() -> None:
    class Handler:
        @rpc.method("greeting.say")
        async def say(self, params: str) -> None: ...

    with pytest.raises(ProtocolDefinitionError, match="params must be"):
        rpc.feature("greeting", handlers=(Handler,))


def test_a_result_annotation_is_required() -> None:
    class Handler:
        @rpc.method("greeting.say")
        async def say(self, params: SayParams): ...

    with pytest.raises(ProtocolDefinitionError, match="needs a return annotation"):
        rpc.feature("greeting", handlers=(Handler,))


def test_a_handler_takes_exactly_self_and_params() -> None:
    class Handler:
        @rpc.method("greeting.say")
        async def say(self, params: SayParams, extra: int) -> None: ...

    with pytest.raises(ProtocolDefinitionError, match="only self and params"):
        rpc.feature("greeting", handlers=(Handler,))


def test_notification_payloads_must_be_decorated_events() -> None:
    class Undecorated(BaseModel):
        text: str

    with pytest.raises(ProtocolDefinitionError, match="not decorated"):
        rpc.feature(
            "greeting",
            notifications=(rpc.notification("greeting.changed", Undecorated),),
        )


def test_a_result_must_be_a_pydantic_model_or_none() -> None:
    class Handler:
        @rpc.method("greeting.say")
        async def say(self, params: SayParams) -> str: ...

    with pytest.raises(ProtocolDefinitionError, match="result must be"):
        rpc.feature("greeting", handlers=(Handler,))


def test_notification_payloads_must_be_pydantic_models() -> None:
    with pytest.raises(ProtocolDefinitionError, match="must contain Pydantic event models"):
        rpc.feature("greeting", notifications=(rpc.notification("greeting.changed", str),))


def test_an_annotated_union_of_events_still_expands_into_events() -> None:
    feature = rpc.feature(
        "greeting",
        notifications=(
            rpc.notification(
                "greeting.changed",
                Annotated[GreetingSaid | GreetingForgotten, "wire payload"],
            ),
        ),
    )

    assert [event.name for event in feature.events] == [
        "greeting.said",
        "greeting.forgotten",
    ]
