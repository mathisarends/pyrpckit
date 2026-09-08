from typing import Literal

import pytest
from pydantic import BaseModel

import pyrpckit as rpc
from pyrpckit import ProtocolDefinitionError
from pyrpckit.decorators import _EVENT_METADATA_KEY, event_metadata

from .conftest import GreetingSaid, SayParams, UnknownGreetingError


def test_router_methods_expose_their_metadata() -> None:
    router = rpc.RpcRouter(prefix="greeting")

    class Handler:
        @router.method("say", summary="Greet someone.")
        async def say(self, params: SayParams) -> None: ...

        @router.method("forget", errors=(UnknownGreetingError,))
        async def forget(self, params: SayParams) -> None: ...

    assert router.routes[0].summary == "Greet someone."
    assert router.routes[1].errors == (UnknownGreetingError,)


def test_a_summary_falls_back_to_the_first_docstring_line() -> None:
    router = rpc.RpcRouter()

    @router.method
    async def say(params: SayParams) -> None:
        """Greet someone.

        The rest of the docstring is not part of the contract.
        """

    assert router.routes[0].summary == "Greet someone."


def test_a_method_without_a_docstring_has_no_summary() -> None:
    router = rpc.RpcRouter()

    @router.method
    async def say(params: SayParams) -> None: ...

    assert router.routes[0].summary is None


def test_an_explicit_summary_wins_over_the_docstring() -> None:
    router = rpc.RpcRouter()

    @router.method(summary="From the decorator.")
    async def say(params: SayParams) -> None:
        """From the docstring."""

    assert router.routes[0].summary == "From the decorator."


def test_a_declared_error_must_be_an_rpc_error_subclass() -> None:
    router = rpc.RpcRouter()

    with pytest.raises(ProtocolDefinitionError, match="must be an RpcError subclass"):

        @router.method(errors=(ValueError,))
        async def say(params: SayParams) -> None: ...


def test_a_declared_error_must_carry_a_code() -> None:
    class Codeless(rpc.RpcError):
        pass

    router = rpc.RpcRouter()
    with pytest.raises(ProtocolDefinitionError, match="declares no code"):

        @router.method(errors=(Codeless,))
        async def say(params: SayParams) -> None: ...


def test_event_metadata_exposes_the_discriminator() -> None:
    metadata = event_metadata(GreetingSaid)
    assert metadata is not None
    assert metadata.name == "greeting.said"


def test_an_event_needs_a_literal_discriminator() -> None:
    with pytest.raises(ProtocolDefinitionError, match="pinned to a string literal"):

        @rpc.event
        class Invalid(BaseModel):
            type: str


def test_corrupted_event_metadata_is_rejected() -> None:
    class Changed(BaseModel):
        type: Literal["changed"] = "changed"

    setattr(Changed, _EVENT_METADATA_KEY, "not-metadata")

    with pytest.raises(ProtocolDefinitionError, match="Invalid RPC event metadata"):
        event_metadata(Changed)
