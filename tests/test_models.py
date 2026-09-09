import pytest
from pydantic import ValidationError

import pyrpckit as rpc
from pyrpckit.schema import render_openrpc


class NavigateParams(rpc.RpcModel):
    project_id: str
    ignore_cache: bool


class NavigateResult(rpc.RpcModel):
    active_project_id: str


NAVIGATION_ROUTER = rpc.RpcRouter(namespace="browser.nav")


class Navigation:
    def __init__(self) -> None:
        self.params: NavigateParams | None = None

    @NAVIGATION_ROUTER.method("navigate")
    async def navigate(self, params: NavigateParams) -> NavigateResult:
        self.params = params
        return NavigateResult(active_project_id=params.project_id)


NAVIGATION_APP = rpc.RpcApp()
NAVIGATION_APP.include_router(NAVIGATION_ROUTER)


def test_rpc_models_use_snake_case_in_python_and_camel_case_on_the_wire() -> None:
    by_name = NavigateParams(project_id="p1", ignore_cache=True)
    by_alias = NavigateParams.model_validate({"projectId": "p2", "ignoreCache": False})

    assert by_name.project_id == "p1"
    assert by_alias.project_id == "p2"
    assert by_name.model_dump() == {"projectId": "p1", "ignoreCache": True}
    with pytest.raises(ValidationError):
        NavigateParams(project_id="p1", ignore_cache=True, unexpected=True)


def test_openrpc_uses_the_canonical_wire_field_names() -> None:
    document = render_openrpc(NAVIGATION_APP.protocol, title="Navigation")
    method = document["methods"][0]

    assert method["params"] == [
        {
            "name": "projectId",
            "required": True,
            "schema": {"title": "Projectid", "type": "string"},
        },
        {
            "name": "ignoreCache",
            "required": True,
            "schema": {"title": "Ignorecache", "type": "boolean"},
        },
    ]
    assert set(document["components"]["schemas"]["NavigateResult"]["properties"]) == {
        "activeProjectId"
    }


async def test_server_accepts_camel_case_and_serializes_results_with_aliases() -> None:
    handler = Navigation()
    response = await NAVIGATION_APP.bind(handler).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "browser.nav.navigate",
            "params": {"projectId": "p1", "ignoreCache": True},
        }
    )

    assert handler.params == NavigateParams(project_id="p1", ignore_cache=True)
    assert response is not None
    assert response.model_dump(mode="json")["result"] == {"activeProjectId": "p1"}


def test_contract_rejects_colliding_wire_field_names() -> None:
    class CollidingParams(rpc.RpcModel):
        foo_bar: str
        fooBar: str

    router = rpc.RpcRouter(namespace="collision")

    class CollidingHandler:
        @router.method("test")
        async def test(self, params: CollidingParams) -> None: ...

    app = rpc.RpcApp()
    app.include_router(router)
    with pytest.raises(rpc.ProtocolDefinitionError, match="wire field 'fooBar'"):
        render_openrpc(app.protocol, title="Collision")
