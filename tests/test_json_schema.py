import pytest
from pydantic import create_model

import pyrpckit as rpc
from pyrpckit import ProtocolDefinitionError, RpcProtocol, rpc_feature
from pyrpckit.schema.json_schema import render_json_schema, type_name


def test_type_name_requires_an_annotation_with_a_stable_name() -> None:
    with pytest.raises(ProtocolDefinitionError, match="no stable schema name"):
        type_name(42)


def test_rendering_rejects_two_distinct_types_sharing_a_schema_name() -> None:
    ParamsA = create_model("Dup", value=(int, ...))
    ParamsB = create_model("Dup", value=(str, ...))

    class Handler(rpc.RpcHandler):
        @rpc.method("greeting.a", summary="A.")
        async def a(self, params: ParamsA) -> None: ...

        @rpc.method("greeting.b", summary="B.")
        async def b(self, params: ParamsB) -> None: ...

    protocol = RpcProtocol((rpc_feature("greeting", handlers=(Handler,)),))

    with pytest.raises(ProtocolDefinitionError, match="Duplicate protocol schema name"):
        render_json_schema(protocol, title="Greeting")
