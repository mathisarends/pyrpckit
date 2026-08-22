from typing import Literal

import pytest
from pydantic import BaseModel

import pyrpckit as rpc
from pyrpckit import ProtocolDefinitionError, decorated_methods, event_metadata

from .conftest import GreetingRpcMethod, GreetingRpcMethods, GreetingSaid, SayParams


def test_decorated_methods_expose_their_metadata() -> None:
    by_name = {
        decorated.attribute_name: decorated.metadata
        for decorated in decorated_methods(GreetingRpcMethods)
    }

    assert by_name["say"].name == GreetingRpcMethod.SAY
    assert by_name["say"].summary == "Greet someone by name."
    assert by_name["say"].errors == (int(rpc.RpcErrorCode.INTERNAL_ERROR),)
    assert by_name["forget"].errors == ()


def test_undecorated_methods_are_ignored() -> None:
    class Handler(rpc.RpcHandler):
        async def helper(self, params: SayParams) -> None: ...

    assert list(decorated_methods(Handler)) == []


def test_a_method_cannot_be_decorated_twice() -> None:
    with pytest.raises(ProtocolDefinitionError):

        class Handler(rpc.RpcHandler):
            @rpc.method("a", summary="A.")
            @rpc.method("b", summary="B.")
            async def say(self, params: SayParams) -> None: ...


def test_event_metadata_is_attached_to_the_payload_model() -> None:
    metadata = event_metadata(GreetingSaid)

    assert metadata is not None
    assert metadata.name == "greeting.said"


def test_event_metadata_is_not_inherited_from_a_base_model() -> None:
    class Subclass(GreetingSaid):
        pass

    assert event_metadata(Subclass) is None


def test_an_event_must_pin_its_type_field_to_the_event_name() -> None:
    with pytest.raises(ProtocolDefinitionError, match="declares type"):

        @rpc.event("greeting.said")
        class Mismatched(BaseModel):
            type: Literal["greeting.other"] = "greeting.other"
