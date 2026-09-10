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
from pyrpckit.schema.export import (
    load_contract_source,
    load_protocol,
    render_contract,
)

APP = rpc.RpcChannel(
    name="health-control", namespace="health", version=2, tags=("system",)
)


@APP.method("ping")
async def ping() -> None: ...


CONTRACT = rpc.RpcContract.from_channels(
    channels=[APP],
    title="Health API",
    description="Service health over WebSocket.",
    server_urls={"health-control": "wss://{host}/projects/{projectId}/health"},
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
)


def test_channel_contract_preserves_explicit_server_metadata() -> None:
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

    control = rpc.RpcChannel(name="control", namespace="browser")
    stream = rpc.RpcChannel(name="stream", namespace="stream")

    @control.method("navigate")
    async def navigate() -> None: ...

    @stream.event("changed", payload=Changed)
    async def changed() -> AsyncIterator[Changed]:
        yield Changed()

    document = json.loads(
        render_contract(
            rpc.RpcContract.from_channels(
                channels=[control, stream],
                title="Browser",
                server_urls={
                    "control": "wss://example.com/browser/control",
                    "stream": "wss://example.com/browser/stream",
                },
            )
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


@pytest.mark.parametrize(
    ("urls", "variables", "subprotocols", "message"),
    [
        ({}, None, None, "match channel names"),
        (
            {
                "api": "",
            },
            None,
            None,
            "non-empty",
        ),
        (
            {"api": "wss://host"},
            {"unused": rpc.ServerVariable("x")},
            None,
            "not present",
        ),
        ({"api": "wss://host"}, {"host": "invalid"}, None, "ServerVariable"),
        ({"api": "wss://host"}, None, {"unknown": "rpc"}, "subprotocols"),
    ],
)
def test_channel_contract_rejects_invalid_metadata(
    urls, variables, subprotocols, message
) -> None:
    with pytest.raises(rpc.ProtocolDefinitionError, match=message):
        rpc.RpcContract.from_channels(
            channels=[rpc.RpcChannel(name="api")],
            title="API",
            server_urls=urls,
            variables=variables,
            subprotocols=subprotocols,
        )


def test_channel_contract_requires_unique_names_and_matching_versions() -> None:
    for channels, urls, message in [
        ([], {}, "at least one"),
        (
            [rpc.RpcChannel(name="api"), rpc.RpcChannel(name="api")],
            {"api": "wss://host"},
            "Duplicate",
        ),
        (
            [rpc.RpcChannel(name="one"), rpc.RpcChannel(name="two", version=2)],
            {"one": "wss://host/one", "two": "wss://host/two"},
            "protocol version",
        ),
    ]:
        with pytest.raises(rpc.ProtocolDefinitionError, match=message):
            rpc.RpcContract.from_channels(
                channels=channels, title="API", server_urls=urls
            )


def test_channel_contract_preserves_url_variables_and_subprotocols() -> None:
    channel = rpc.RpcChannel(name="api")
    contract = rpc.RpcContract.from_channels(
        channels=[channel],
        title="API",
        server_urls={"api": "wss://host/external/{session_id}"},
        subprotocols={"api": "jsonrpc"},
    )
    server = contract.servers[0]
    assert server["url"] == "wss://host/external/{session_id}"
    assert server["variables"] == {"session_id": {"default": "{session_id}"}}
    assert server["x-rpckit-transport"]["subprotocols"] == ["jsonrpc"]


def test_channel_contract_disambiguates_request_types() -> None:
    one = rpc.RpcChannel(name="one", namespace="one")
    two = rpc.RpcChannel(name="two", namespace="two")

    @one.method("ping")
    async def ping() -> None: ...

    two.method("ping")(ping)
    contract = rpc.RpcContract.from_channels(
        channels=[one, two],
        title="API",
        server_urls={"one": "wss://host/one", "two": "wss://host/two"},
    )
    assert {method.request_name for method in contract.protocol.methods} == {
        "OnePingRequest",
        "TwoPingRequest",
    }
