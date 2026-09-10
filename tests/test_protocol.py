from collections.abc import AsyncIterator
from typing import Annotated

import pytest
from pydantic import BaseModel, Field

import pyrpckit as rpc
from pyrpckit.protocol import RpcProtocol

from .conftest import (
    ForgetParams,
    GreetedResult,
    GreetingForgotten,
    GreetingNotificationMethod,
    GreetingRpcMethod,
    GreetingSaid,
    SayParams,
    SayResult,
)


def test_the_protocol_collects_the_apps_methods(protocol: RpcProtocol) -> None:
    assert [method.name for method in protocol.methods] == [
        GreetingRpcMethod.SAY,
        GreetingRpcMethod.FORGET,
        GreetingRpcMethod.GREETED,
        GreetingRpcMethod.CLEAR,
    ]


def test_a_method_definition_describes_its_wire_contract(
    protocol: RpcProtocol,
) -> None:
    say = protocol.method(GreetingRpcMethod.SAY)

    assert say.handler_name == "say"
    assert say.request_name == "SayRequest"
    assert say.params is not None
    assert issubclass(say.params, SayParams)
    assert issubclass(say.result, SayResult)
    assert say.tags == ("greeting",)


def test_a_method_may_return_nothing(protocol: RpcProtocol) -> None:
    forget = protocol.method(GreetingRpcMethod.FORGET)

    assert forget.params is not None
    assert issubclass(forget.params, ForgetParams)
    assert forget.result is type(None)


def test_notifications_expand_into_their_union_types(
    protocol: RpcProtocol,
) -> None:
    assert [notification.name for notification in protocol.notifications] == [
        GreetingNotificationMethod.CHANGED
    ]
    assert [item.name for item in protocol.notification_types] == [
        "greeting.said",
        "greeting.forgotten",
    ]


def test_an_unknown_method_is_rejected(protocol: RpcProtocol) -> None:
    with pytest.raises(rpc.RpcMethodNotFoundError):
        protocol.method("greeting.unknown")


def test_a_single_notification_payload_needs_no_literal_discriminator() -> None:
    class Undiscriminated(BaseModel):
        text: str

    router = rpc.RpcRouter()

    @router.notification("changed", payload=Undiscriminated)
    async def changed() -> AsyncIterator[Undiscriminated]:
        yield Undiscriminated(text="")

    app = rpc.RpcApp()
    app.include_router(router)

    assert app.protocol.notifications[0].payload is Undiscriminated
    assert app.protocol.notification_types == ()


def test_notification_union_members_need_a_literal_discriminator() -> None:
    class First(BaseModel):
        text: str

    class Second(BaseModel):
        value: str

    router = rpc.RpcRouter()

    @router.notification("changed", payload=First | Second)
    async def changed() -> AsyncIterator[First | Second]:
        yield First(text="")

    app = rpc.RpcApp()
    app.include_router(router)

    with pytest.raises(rpc.ProtocolDefinitionError, match="when used in a union"):
        _ = app.protocol


def test_notification_payloads_must_be_pydantic_models() -> None:
    router = rpc.RpcRouter()

    @router.notification("changed", payload=str)
    async def changed() -> AsyncIterator[str]:
        yield "changed"

    app = rpc.RpcApp()
    app.include_router(router)

    with pytest.raises(
        rpc.ProtocolDefinitionError,
        match="must contain Pydantic models",
    ):
        _ = app.protocol


def test_an_annotated_union_still_expands_into_notification_types() -> None:
    router = rpc.RpcRouter()
    type GreetingUpdate = Annotated[
        GreetingSaid | GreetingForgotten,
        Field(discriminator="type"),
    ]

    @router.notification("changed", payload=GreetingUpdate)
    async def changed() -> AsyncIterator[GreetingUpdate]:
        yield GreetingSaid(text="")

    app = rpc.RpcApp()
    app.include_router(router)

    assert [item.name for item in app.protocol.notification_types] == [
        "greeting.said",
        "greeting.forgotten",
    ]


def test_a_method_may_take_no_params(protocol: RpcProtocol) -> None:
    greeted = protocol.method(GreetingRpcMethod.GREETED)

    assert greeted.params is None
    assert issubclass(greeted.result, GreetedResult)


def test_a_method_may_take_no_params_and_return_nothing(
    protocol: RpcProtocol,
) -> None:
    clear = protocol.method(GreetingRpcMethod.CLEAR)

    assert clear.params is None
    assert clear.result is type(None)
