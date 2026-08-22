import json
from pathlib import Path

import httpx
import pytest

from examples.calculator_client import CalculatorClient
from examples.fastapi_app.app import app
from examples.fastapi_app.transport import HttpJsonRpcTransport
from pyrpckit.client import RpcRemoteError
from pyrpckit.codegen import generate_python_client
from pyrpckit.codegen.python import PythonClientOptions

EXAMPLE = Path(__file__).parents[2] / "examples" / "fastapi_app"


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


async def test_the_app_publishes_its_openrpc_contract() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/openrpc.json")

    committed = json.loads((EXAMPLE / "calculator.openrpc.json").read_text(encoding="utf-8"))
    assert response.status_code == 200
    assert response.json() == committed
    assert [method["name"] for method in committed["methods"]] == [
        "calculator.add",
        "calculator.divide",
    ]


def test_the_committed_client_matches_the_committed_contract() -> None:
    document = json.loads((EXAMPLE / "calculator.openrpc.json").read_text(encoding="utf-8"))
    options = PythonClientOptions(
        package="examples.calculator_client",
        client_name="CalculatorClient",
        source="calculator.openrpc.json",
    )

    assert (
        generate_python_client(
            document,
            EXAMPLE.parent / "calculator_client",
            options,
            check=True,
        )
        == ()
    )
