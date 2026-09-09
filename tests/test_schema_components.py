import pytest
from pydantic import create_model

import pyrpckit as rpc
from pyrpckit import ProtocolDefinitionError
from pyrpckit.schema import render_openrpc
from pyrpckit.schema._components import type_name


def test_type_name_requires_an_annotation_with_a_stable_name() -> None:
    with pytest.raises(ProtocolDefinitionError, match="no stable schema name"):
        type_name(42)


def test_rendering_rejects_two_distinct_types_sharing_a_schema_name() -> None:
    ParamsA = create_model("Dup", value=(int, ...))
    ParamsB = create_model("Dup", value=(str, ...))

    router = rpc.RpcRouter(namespace="greeting")

    class Handler:
        @router.method("a")
        async def a(self, params: ParamsA) -> None: ...

        @router.method("b")
        async def b(self, params: ParamsB) -> None: ...

    app = rpc.RpcApp()
    app.include_router(router)
    with pytest.raises(ProtocolDefinitionError, match="Duplicate protocol schema name"):
        render_openrpc(app.protocol, title="Greeting")
