import json
from pathlib import Path

import httpx
import pytest

from pyrpckit.client import RpcRemoteError
from pyrpckit.codegen import generate_python_client
from pyrpckit.codegen.python import PythonClientOptions
from pyrpckit.schema.export import render_contract
from scripts.fastapi_showcase.generate import DESCRIPTION, SERVERS, TITLE
from showcase.app.api import PROTOCOL
from showcase.app.server import app
from showcase.client import CalculatorClient
from showcase.client.transport import HttpJsonRpcTransport

SHOWCASE = Path(__file__).parents[2] / "showcase"


@pytest.fixture
def transport() -> HttpJsonRpcTransport:
    http_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    )
    return HttpJsonRpcTransport("/rpc", client=http_client)


async def test_generated_client_calls_the_fastapi_app(
    transport: HttpJsonRpcTransport,
) -> None:
    async with CalculatorClient(transport) as client:
        result = await client.calculator.add(left=20, right=22)

    assert result.value == 42


async def test_declared_errors_reach_the_generated_client(
    transport: HttpJsonRpcTransport,
) -> None:
    async with CalculatorClient(transport) as client:
        with pytest.raises(RpcRemoteError, match="Cannot divide by zero") as raised:
            await client.calculator.divide(left=1, right=0)

    assert raised.value.code == -32001


def test_the_committed_contract_matches_the_declared_api() -> None:
    committed = json.loads(
        (SHOWCASE / "spec" / "calculator.openrpc.json").read_text(encoding="utf-8")
    )

    assert committed == json.loads(
        render_contract(
            PROTOCOL,
            "openrpc",
            title=TITLE,
            description=DESCRIPTION,
            servers=SERVERS,
        )
    )
    assert [method["name"] for method in committed["methods"]] == [
        "calculator.add",
        "calculator.divide",
    ]


def test_the_committed_client_matches_the_committed_contract() -> None:
    document = json.loads(
        (SHOWCASE / "spec" / "calculator.openrpc.json").read_text(encoding="utf-8")
    )
    options = PythonClientOptions(
        package="showcase.client",
        client_name="CalculatorClient",
        source="calculator.openrpc.json",
    )

    assert (
        generate_python_client(
            document,
            SHOWCASE / "client",
            options,
            check=True,
        )
        == ()
    )
