from enum import StrEnum
from typing import Literal

import pytest
from pydantic import BaseModel

import pyrpckit as rpc
from pyrpckit import RpcProtocol


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


@rpc.event
class GreetingSaid(BaseModel):
    type: Literal["greeting.said"] = "greeting.said"
    text: str


@rpc.event
class GreetingForgotten(BaseModel):
    type: Literal["greeting.forgotten"] = "greeting.forgotten"
    name: str


type GreetingEvent = GreetingSaid | GreetingForgotten


class UnknownGreetingError(rpc.RpcError):
    code = -32001
    message = "Unknown greeting"


class GreetingRpcMethods(rpc.RpcHandler):
    def __init__(self) -> None:
        self.greeted: list[str] = []

    @rpc.method(GreetingRpcMethod.SAY, summary="Greet someone by name.")
    async def say(self, params: SayParams) -> SayResult:
        self.greeted.append(params.name)
        return SayResult(text=f"Hello, {params.name}!")

    @rpc.method(GreetingRpcMethod.FORGET, errors=(UnknownGreetingError,))
    async def forget(self, params: ForgetParams) -> None:
        """Forget a greeted name."""
        if params.name not in self.greeted:
            raise UnknownGreetingError(f"Unknown greeting: {params.name}")
        self.greeted.remove(params.name)


GREETING_FEATURE = rpc.feature(
    "greeting",
    handlers=(GreetingRpcMethods,),
    notifications=(
        rpc.notification(
            GreetingNotificationMethod.CHANGED,
            GreetingEvent,
            summary="Publish a greeting change.",
        ),
    ),
)


@pytest.fixture
def protocol() -> RpcProtocol:
    return RpcProtocol(GREETING_FEATURE)


@pytest.fixture
def handler() -> GreetingRpcMethods:
    return GreetingRpcMethods()
