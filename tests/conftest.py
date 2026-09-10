from collections.abc import AsyncIterator
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


class GreetingSaid(BaseModel):
    type: Literal["greeting.said"] = "greeting.said"
    text: str


class GreetingForgotten(BaseModel):
    type: Literal["greeting.forgotten"] = "greeting.forgotten"
    name: str


type GreetingUpdate = GreetingSaid | GreetingForgotten


class UnknownGreetingError(rpc.RpcError):
    code = -32001
    message = "Unknown greeting"


GREETING_ROUTER = rpc.RpcRouter(namespace="greeting", tags=("greeting",))


class GreetingState:
    def __init__(self) -> None:
        self.greeted: list[str] = []


@GREETING_ROUTER.method("say", summary="Greet someone by name.")
async def say(
    params: SayParams,
    state: rpc.Inject[GreetingState],
) -> SayResult:
    state.greeted.append(params.name)
    return SayResult(text=f"Hello, {params.name}!")


@GREETING_ROUTER.method("forget", errors=(UnknownGreetingError,))
async def forget(
    params: ForgetParams,
    state: rpc.Inject[GreetingState],
) -> None:
    """Forget a greeted name."""
    if params.name not in state.greeted:
        raise UnknownGreetingError(f"Unknown greeting: {params.name}")
    state.greeted.remove(params.name)


@GREETING_ROUTER.method("greeted")
async def greeted_names(
    state: rpc.Inject[GreetingState],
) -> GreetedResult:
    """List everyone greeted so far."""
    return GreetedResult(names=list(state.greeted))


@GREETING_ROUTER.method("clear")
async def clear(state: rpc.Inject[GreetingState]) -> None:
    """Forget everyone."""
    state.greeted.clear()


@GREETING_ROUTER.notification(
    "changed",
    payload=GreetingUpdate,
    summary="Publish a greeting change.",
)
async def greeting_changed() -> AsyncIterator[GreetingUpdate]:
    if False:
        yield GreetingSaid(text="")


GREETING_APP = rpc.RpcApp()
GREETING_APP.include_router(GREETING_ROUTER)
GREETING_PROTOCOL = GREETING_APP.protocol


@pytest.fixture
def protocol() -> RpcProtocol:
    return GREETING_PROTOCOL


@pytest.fixture
def handler() -> GreetingState:
    return GreetingState()


class TestResolver:
    __test__ = False

    def __init__(self, *values: object) -> None:
        self.values = {type(value): value for value in values}

    async def resolve[DependencyT](
        self,
        dependency: type[DependencyT],
    ) -> DependencyT:
        return self.values[dependency]  # type: ignore[return-value]
