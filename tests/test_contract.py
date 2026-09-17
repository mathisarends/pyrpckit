from collections.abc import AsyncIterator

import pytest

from pyrpckit import RpcChannel, RpcError, RpcModel, RpcService, ServerVariable
from pyrpckit.schema import render_openrpc
from pyrpckit.schema.export import render_contract


class Missing(RpcModel):
    resource_id: str


class MissingError(RpcError):
    details: Missing


channel = RpcChannel("control")


@channel.method(raises=[MissingError])
async def ping() -> None: ...


@channel.stream(content_type="image/jpeg")
async def frames() -> AsyncIterator[bytes]:
    yield b"frame"


service = RpcService(version=2)
service.socket("/projects/{project_id}/rpc", channel, subprotocol="rpc.v2")
service.stream("/projects/{project_id}/frames", frames)
contract = service.contract(
    title="Control API",
    base_url="wss://api.example.com",
    variables={"project_id": ServerVariable(default="demo")},
)
CONTRACT = contract


def test_contract_derives_servers_and_streams() -> None:
    document = render_openrpc(
        contract.protocol,
        title=contract.title,
        servers=contract.servers,
        binary_streams=contract.binary_streams,
    )
    assert document["info"]["version"] == "2.0.0"
    assert document["servers"][0]["url"].endswith("/projects/{project_id}/rpc")
    assert document["x-rpckit-binary-streams"][0]["name"] == "control.frames"
    error = document["methods"][0]["errors"][0]
    assert error["x-rpckit-code"] == "missing"
    assert error["x-rpckit-details-schema"]["$ref"].endswith("/Missing")


def test_render_contract_uses_service_contract() -> None:
    text = render_contract(contract)
    assert '"x-rpckit-code": "missing"' in text


def test_unused_variable_is_rejected() -> None:
    with pytest.raises(Exception, match="not present"):
        service.contract(
            title="Control API",
            base_url="wss://api.example.com",
            variables={"unused": ServerVariable(default="x")},
        )
