from enum import StrEnum
from typing import Literal

import pytest
from pydantic import BaseModel

import pyrpckit as rpc
from pyrpckit.protocol import RpcProtocol


class GreetingRpcMethod(StrEnum):
    SAY = "greeting.say"
    FORGET = "greeting.forget"
    GREETED = "greeting.greeted"
    CLEAR = "greeting.clear"


class GreetingNotificationMethod(StrEnum):
    CHANGED = "greeting.changed"


class SayParams(BaseModel):
    name: str


class SayResult(BaseModel):
    text: str


class ForgetParams(BaseModel):
    name: str


class GreetedResult(BaseModel):
    names: list[str]


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


GREETING_ROUTER = rpc.RpcRouter(namespace="greeting", tags=("greeting",))


class GreetingRpcMethods:
    def __init__(self) -> None:
        self.greeted: list[str] = []

    @GREETING_ROUTER.method("say", summary="Greet someone by name.")
    async def say(self, params: SayParams) -> SayResult:
        self.greeted.append(params.name)
        return SayResult(text=f"Hello, {params.name}!")

    @GREETING_ROUTER.method("forget", errors=(UnknownGreetingError,))
    async def forget(self, params: ForgetParams) -> None:
        """Forget a greeted name."""
        if params.name not in self.greeted:
            raise UnknownGreetingError(f"Unknown greeting: {params.name}")
        self.greeted.remove(params.name)

    @GREETING_ROUTER.method("greeted")
    async def greeted_names(self) -> GreetedResult:
        """List everyone greeted so far."""
        return GreetedResult(names=list(self.greeted))

    @GREETING_ROUTER.method("clear")
    async def clear(self) -> None:
        """Forget everyone."""
        self.greeted.clear()


GREETING_ROUTER.event(
    "changed",
    GreetingEvent,
    summary="Publish a greeting change.",
)
GREETING_APP = rpc.RpcApp()
GREETING_APP.include_router(GREETING_ROUTER)
GREETING_PROTOCOL = GREETING_APP.protocol


@pytest.fixture
def protocol() -> RpcProtocol:
    return GREETING_PROTOCOL


@pytest.fixture
def handler() -> GreetingRpcMethods:
    return GreetingRpcMethods()
