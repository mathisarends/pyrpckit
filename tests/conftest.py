from enum import StrEnum
from typing import Literal

import pytest
from pydantic import BaseModel

import pyrpckit as rpc
from pyrpckit import RpcErrorCode, RpcProtocol, rpc_feature


class GreetingRpcMethod(StrEnum):
    SAY = "greeting.say"
    FORGET = "greeting.forget"


class GreetingNotificationMethod(StrEnum):
    CHANGED = "greeting.changed"


class SayParams(BaseModel):
    name: str


class SayResult(BaseModel):
    text: str


class ForgetParams(BaseModel):
    name: str


@rpc.event("greeting.said")
class GreetingSaid(BaseModel):
    type: Literal["greeting.said"] = "greeting.said"
    text: str


@rpc.event("greeting.forgotten")
class GreetingForgotten(BaseModel):
    type: Literal["greeting.forgotten"] = "greeting.forgotten"
    name: str


type GreetingEvent = GreetingSaid | GreetingForgotten


class UnknownGreetingError(Exception):
    pass


class GreetingRpcMethods(rpc.RpcHandler):
    def __init__(self) -> None:
        self.greeted: list[str] = []

    @rpc.method(
        GreetingRpcMethod.SAY,
        summary="Greet someone by name.",
        errors=(RpcErrorCode.INTERNAL_ERROR,),
    )
    async def say(self, params: SayParams) -> SayResult:
        self.greeted.append(params.name)
        return SayResult(text=f"Hello, {params.name}!")

    @rpc.method(GreetingRpcMethod.FORGET, summary="Forget a greeted name.")
    async def forget(self, params: ForgetParams) -> None:
        if params.name not in self.greeted:
            raise UnknownGreetingError(params.name)
        self.greeted.remove(params.name)


GREETING_FEATURE = rpc_feature(
    "greeting",
    handlers=(GreetingRpcMethods,),
    notifications=(
        rpc.RpcNotificationDefinition(
            name=GreetingNotificationMethod.CHANGED,
            payload=GreetingEvent,
            summary="Publish a greeting change.",
        ),
    ),
)


@pytest.fixture
def protocol() -> RpcProtocol:
    return RpcProtocol((GREETING_FEATURE,))


@pytest.fixture
def handler() -> GreetingRpcMethods:
    return GreetingRpcMethods()
