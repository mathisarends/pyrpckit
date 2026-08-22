from typing import Literal

import pytest
from pydantic import BaseModel

import pyrpckit as rpc
from pyrpckit import ProtocolDefinitionError
from pyrpckit.decorators import (
    _EVENT_METADATA_KEY,
    _METHOD_METADATA_KEY,
    RpcMethodMetadata,
    _method_metadata,
    decorated_methods,
    event_metadata,
)

from .conftest import (
    GreetingRpcMethod,
    GreetingRpcMethods,
    GreetingSaid,
    SayParams,
    UnknownGreetingError,
)


def _metadata_by_name(handler: type) -> dict[str, RpcMethodMetadata]:
    return {
        decorated.attribute_name: decorated.metadata
        for decorated in decorated_methods(handler)
    }


def test_decorated_methods_expose_their_metadata() -> None:
    by_name = _metadata_by_name(GreetingRpcMethods)

    assert by_name["say"].name == GreetingRpcMethod.SAY
    assert by_name["say"].summary == "Greet someone by name."
    assert by_name["say"].errors == ()
    assert by_name["forget"].errors == (UnknownGreetingError,)


def test_a_summary_falls_back_to_the_first_docstring_line() -> None:
    class Handler:
        @rpc.method("greeting.say")
        async def say(self, params: SayParams) -> None:
            """Greet someone.

            The rest of the docstring is not part of the contract.
            """

    assert _metadata_by_name(Handler)["say"].summary == "Greet someone."


def test_a_method_without_a_docstring_has_no_summary() -> None:
    class Handler:
        @rpc.method("greeting.say")
        async def say(self, params: SayParams) -> None: ...

    assert _metadata_by_name(Handler)["say"].summary is None


def test_an_explicit_summary_wins_over_the_docstring() -> None:
    class Handler:
        @rpc.method("greeting.say", summary="From the decorator.")
        async def say(self, params: SayParams) -> None:
            """From the docstring."""

    assert _metadata_by_name(Handler)["say"].summary == "From the decorator."


def test_a_declared_error_must_be_an_rpc_error_subclass() -> None:
    with pytest.raises(ProtocolDefinitionError, match="must be an RpcError subclass"):

        class Handler:
            @rpc.method("greeting.say", errors=(ValueError,))
            async def say(self, params: SayParams) -> None: ...


def test_a_declared_error_must_carry_a_code() -> None:
    class Codeless(rpc.RpcError):
        pass

    with pytest.raises(ProtocolDefinitionError, match="declares no code"):

        class Handler:
            @rpc.method("greeting.say", errors=(Codeless,))
            async def say(self, params: SayParams) -> None: ...


def test_undecorated_methods_are_ignored() -> None:
    class Handler:
        async def helper(self, params: SayParams) -> None: ...

    assert list(decorated_methods(Handler)) == []


def test_a_method_cannot_be_decorated_twice() -> None:
    with pytest.raises(ProtocolDefinitionError, match="already decorated"):

        class Handler:
            @rpc.method("a")
            @rpc.method("b")
            async def say(self, params: SayParams) -> None: ...


def test_event_metadata_takes_its_name_from_the_pinned_type_field() -> None:
    metadata = event_metadata(GreetingSaid)

    assert metadata is not None
    assert metadata.name == "greeting.said"


def test_event_metadata_is_not_inherited_from_a_base_model() -> None:
    class Subclass(GreetingSaid):
        pass

    assert event_metadata(Subclass) is None


def test_an_event_must_pin_its_type_field_to_a_string_literal() -> None:
    with pytest.raises(ProtocolDefinitionError, match="needs a type field"):

        @rpc.event
        class Unpinned(BaseModel):
            type: str


def test_an_event_cannot_be_decorated_twice() -> None:
    with pytest.raises(ProtocolDefinitionError, match="already decorated"):

        @rpc.event
        @rpc.event
        class Twice(BaseModel):
            type: Literal["greeting.twice"] = "greeting.twice"


def test_event_metadata_rejects_a_corrupted_metadata_value() -> None:
    class Corrupted(BaseModel):
        pass

    setattr(Corrupted, _EVENT_METADATA_KEY, "not-metadata")

    with pytest.raises(ProtocolDefinitionError, match="Invalid RPC event metadata"):
        event_metadata(Corrupted)


def test_method_metadata_rejects_a_corrupted_metadata_value() -> None:
    def handler() -> None: ...

    setattr(handler, _METHOD_METADATA_KEY, "not-metadata")

    with pytest.raises(ProtocolDefinitionError, match="Invalid RPC method metadata"):
        _method_metadata(handler)
