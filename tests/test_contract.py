import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import pytest

import pyrpckit as rpc
from pyrpckit.codegen import render_python_client, render_typescript_client
from pyrpckit.codegen.cli import main
from pyrpckit.codegen.python import PythonClientOptions
from pyrpckit.codegen.typescript import TypeScriptClientOptions
from pyrpckit.fastapi import RpcAPIRouter
from pyrpckit.schema.export import (
    load_contract_source,
    load_protocol,
    render_contract,
)

ROUTER = RpcAPIRouter(prefix="/projects/{project_id}", version=2, tags=("system",))
APP = ROUTER.websocket("/health", name="health-control", namespace="health")


@APP.method("ping")
async def ping() -> None: ...


CONTRACT = ROUTER.contract(
    title="Health API",
    description="Service health over WebSocket.",
    public_base_url="wss://{host}",
    variables={
        "host": rpc.ServerVariable(
            default="api.example.com",
            enum=("api.example.com", "staging.example.com"),
        ),
        "project_id": rpc.ServerVariable(
            default="00000000-0000-0000-0000-000000000000",
            description="Project selected by the caller.",
        ),
    },
)


def test_router_contract_derives_typed_server_metadata_from_the_fastapi_path() -> None:
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
    assert document["methods"][0]["servers"] == document["servers"]


def test_contract_assigns_each_channel_to_its_own_server() -> None:
    class Changed(rpc.RpcModel):
        type: Literal["browser.changed"] = "browser.changed"

    router = RpcAPIRouter(prefix="/browser")
    control = router.websocket("/control", name="control", namespace="browser")
    stream = router.websocket("/stream", name="stream", namespace="stream")

    @control.method("navigate")
    async def navigate() -> None: ...

    @stream.event("changed", payload=Changed)
    async def changed() -> AsyncIterator[Changed]:
        yield Changed()

    document = json.loads(
        render_contract(
            router.contract(title="Browser", public_base_url="wss://example.com")
        )
    )

    assert [server["name"] for server in document["servers"]] == [
        "control",
        "stream",
    ]
    assert document["methods"][0]["servers"][0]["name"] == "control"
    assert document["x-rpc-notifications"][0]["servers"][0]["name"] == "stream"


def test_channel_contract_feeds_the_existing_client_generators() -> None:
    document = json.loads(render_contract(CONTRACT))
    python = render_python_client(
        document,
        PythonClientOptions(package="health_client"),
    )
    typescript = render_typescript_client(document, TypeScriptClientOptions())

    assert "HEALTH_CONTROL" in python["endpoints.py"]
    assert 'server="health-control"' in python["routes.py"]
    assert 'server: "health-control"' in typescript["routes.ts"]
    assert "healthControl" in typescript["endpoints.ts"]


def test_contract_sources_resolve_to_their_protocol() -> None:
    assert load_contract_source("tests.test_contract:APP") is APP
    assert load_contract_source("tests.test_contract:CONTRACT") is CONTRACT
    assert load_protocol("tests.test_contract:APP") is APP.protocol
    assert load_protocol("tests.test_contract:CONTRACT") is CONTRACT.protocol


def test_schema_cli_accepts_a_channel(tmp_path: Path) -> None:
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


def test_schema_cli_requires_a_title_for_a_bare_channel(
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


def test_contract_server_metadata_is_copied() -> None:
    servers = [{"name": "api", "url": "wss://example.com/rpc"}]
    contract = rpc.RpcContract(
        protocol=rpc.RpcChannel().protocol,
        title="API",
        servers=tuple(servers),
    )
    servers[0].clear()

    assert contract.servers[0]["name"] == "api"
    with pytest.raises(TypeError):
        contract.servers[0]["other"] = "value"  # type: ignore[index]


def test_contract_rejects_duplicate_server_names() -> None:
    servers = (
        {"name": "control", "url": "wss://one.example/control"},
        {"name": "control", "url": "wss://two.example/control"},
    )

    with pytest.raises(rpc.ProtocolDefinitionError, match="Duplicate.*control"):
        rpc.RpcContract(
            protocol=rpc.RpcChannel().protocol,
            title="Browser",
            servers=servers,
        )


def test_server_variable_defaults_must_belong_to_the_enum() -> None:
    with pytest.raises(rpc.ProtocolDefinitionError, match="one of its enum"):
        rpc.ServerVariable(default="production", enum=("staging",))
