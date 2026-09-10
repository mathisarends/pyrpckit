import json
from pathlib import Path
from typing import Literal

import pytest

import pyrpckit as rpc
from pyrpckit.codegen.cli import main
from pyrpckit.schema.export import (
    load_contract_source,
    load_protocol,
    render_contract,
)

ROUTER = rpc.RpcRouter(namespace="health", tags=("system",))


@ROUTER.method("ping")
async def ping() -> None: ...


APP = rpc.RpcApp(version=2)
APP.include_router(ROUTER)

CONTRACT = rpc.OpenRpcContract(
    app=APP,
    title="Health API",
    description="Service health over WebSocket.",
    servers=(
        rpc.OpenRpcServer(
            name="health-control",
            url="wss://{host}/projects/{projectId}/health",
            summary="Health checks",
            variables={
                "host": rpc.ServerVariable(
                    default="api.example.com",
                    enum=("api.example.com", "staging.example.com"),
                ),
                "projectId": rpc.ServerVariable(
                    default="00000000-0000-0000-0000-000000000000",
                    description="Project selected by the caller.",
                ),
            },
            extensions={
                "x-rpckit-transport": {
                    "type": "websocket",
                    "messageEncoding": "json",
                    "frameType": "text",
                }
            },
        ),
    ),
)


def test_contract_renders_typed_server_metadata() -> None:
    document = json.loads(render_contract(CONTRACT))

    assert document["info"] == {
        "title": "Health API",
        "version": "2.0.0",
        "description": "Service health over WebSocket.",
    }
    assert document["servers"] == [
        {
            "name": "health-control",
            "url": "wss://{host}/projects/{projectId}/health",
            "summary": "Health checks",
            "variables": {
                "host": {
                    "default": "api.example.com",
                    "enum": ["api.example.com", "staging.example.com"],
                },
                "projectId": {
                    "default": "00000000-0000-0000-0000-000000000000",
                    "description": "Project selected by the caller.",
                },
            },
            "x-rpckit-transport": {
                "type": "websocket",
                "messageEncoding": "json",
                "frameType": "text",
            },
        }
    ]
    assert document["methods"][0]["tags"] == [{"name": "system"}]


def test_contract_assigns_router_routes_to_their_declared_server() -> None:
    class Changed(rpc.RpcModel):
        type: Literal["browser.changed"] = "browser.changed"

    router = rpc.RpcRouter(namespace="browser", server="control")

    @router.method("navigate")
    async def navigate() -> None: ...

    @router.notification("changed")
    def changed() -> Changed: ...

    app = rpc.RpcApp()
    app.include_router(router)
    contract = rpc.OpenRpcContract(
        app=app,
        title="Browser",
        servers=(rpc.OpenRpcServer(name="control", url="wss://example.com/control"),),
    )

    document = json.loads(render_contract(contract))
    assert document["methods"][0]["servers"] == document["servers"]
    assert document["x-rpc-notifications"][0]["servers"] == document["servers"]
    assert app.protocol.methods[0].server == "control"
    assert app.protocol.notifications[0].server == "control"


def test_contract_rejects_an_unknown_router_server() -> None:
    router = rpc.RpcRouter(namespace="browser", server="missing")

    @router.method("navigate")
    async def navigate() -> None: ...

    app = rpc.RpcApp()
    app.include_router(router)

    with pytest.raises(
        rpc.ProtocolDefinitionError,
        match="method 'browser.navigate' -> 'missing'",
    ):
        rpc.OpenRpcContract(app=app, title="Browser")


def test_contract_rejects_duplicate_server_names() -> None:
    app = rpc.RpcApp()
    servers = (
        rpc.OpenRpcServer(name="control", url="wss://one.example/control"),
        rpc.OpenRpcServer(name="control", url="wss://two.example/control"),
    )

    with pytest.raises(rpc.ProtocolDefinitionError, match="Duplicate.*control"):
        rpc.OpenRpcContract(app=app, title="Browser", servers=servers)


def test_contract_sources_resolve_to_their_protocol() -> None:
    assert load_contract_source("tests.test_contract:APP") is APP
    assert load_contract_source("tests.test_contract:CONTRACT") is CONTRACT
    assert load_protocol("tests.test_contract:APP") is APP.protocol
    assert load_protocol("tests.test_contract:CONTRACT") is APP.protocol


def test_schema_cli_accepts_an_app(tmp_path: Path) -> None:
    output = tmp_path / "app.openrpc.json"

    result = main(
        [
            "schema",
            "tests.test_contract:APP",
            "--output",
            str(output),
            "--title",
            "Health App",
        ]
    )

    assert result == 0
    assert json.loads(output.read_text(encoding="utf-8"))["info"]["title"] == (
        "Health App"
    )


def test_schema_cli_uses_contract_metadata_without_flags(tmp_path: Path) -> None:
    output = tmp_path / "contract.openrpc.json"

    result = main(
        [
            "schema",
            "tests.test_contract:CONTRACT",
            "--output",
            str(output),
        ]
    )

    assert result == 0
    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["info"]["title"] == "Health API"
    assert document["servers"][0]["variables"]["host"]["default"] == ("api.example.com")


def test_schema_cli_requires_a_title_for_a_bare_app(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    result = main(
        [
            "schema",
            "tests.test_contract:APP",
            "--output",
            str(tmp_path / "app.openrpc.json"),
        ]
    )

    assert result == 2
    assert "A title is required" in capsys.readouterr().err


def test_server_metadata_is_copied_into_read_only_mappings() -> None:
    variables = {"host": rpc.ServerVariable(default="api.example.com")}
    server = rpc.OpenRpcServer(name="api", url="wss://{host}/rpc", variables=variables)
    variables.clear()

    assert tuple(server.variables) == ("host",)
    with pytest.raises(TypeError):
        server.variables["other"] = rpc.ServerVariable(default="other")


def test_server_extensions_must_use_the_openrpc_prefix() -> None:
    with pytest.raises(rpc.ProtocolDefinitionError, match="must start with 'x-'"):
        rpc.OpenRpcServer(
            name="api",
            url="wss://example.com/rpc",
            extensions={"transport": "websocket"},
        )


@pytest.mark.parametrize(
    ("url", "variables", "problem"),
    [
        ("wss://{host}/rpc", {}, "missing=['host']"),
        (
            "wss://example.com/rpc",
            {"host": rpc.ServerVariable(default="example.com")},
            "unused=['host']",
        ),
    ],
)
def test_url_placeholders_and_variable_declarations_must_match(
    url: str, variables: dict[str, rpc.ServerVariable], problem: str
) -> None:
    with pytest.raises(rpc.ProtocolDefinitionError, match=problem.replace("[", r"\[")):
        rpc.OpenRpcServer(name="api", url=url, variables=variables)


def test_server_variable_defaults_must_belong_to_the_enum() -> None:
    with pytest.raises(rpc.ProtocolDefinitionError, match="one of its enum"):
        rpc.ServerVariable(default="production", enum=("staging",))
