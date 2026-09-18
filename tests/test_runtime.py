import asyncio
from collections.abc import AsyncIterator

from pyrpckit import (
    Inject,
    RpcChannel,
    RpcConnection,
    RpcConnectionClose,
    RpcModel,
    RpcService,
)
from pyrpckit.testing import RpcTestClient


class Params(RpcModel):
    value: str


channel = RpcChannel("demo")
connections = []


@channel.method()
async def echo(params: Params, connection: Inject[RpcConnection]) -> Params:
    connections.append(connection)
    return params


service = RpcService()
service.socket("/rpc", channels=(channel,))


async def test_request_and_connection_context() -> None:
    connections.clear()
    async with RpcTestClient(service, "/rpc", headers={"Authorization": "x"}) as client:
        assert await client.request("demo.echo", {"value": "yes"}) == {"value": "yes"}
    assert connections[0].headers["authorization"] == "x"
    assert connections[0].closed
    assert connections[0].close_code == RpcConnectionClose.NORMAL
    assert connections[0].close_reason == ""


async def test_peer_close_information_is_exposed_on_the_connection() -> None:
    connections.clear()
    async with RpcTestClient(service, "/rpc") as client:
        await client.request("demo.echo", {"value": "yes"})
        await client.socket.client_disconnect(1001, "Going away")

    assert connections[0].close_code == 1001
    assert connections[0].close_reason == "Going away"


async def test_binary_stream() -> None:
    streams = RpcChannel("streams")

    @streams.stream()
    async def frames() -> AsyncIterator[bytes]:
        yield b"one"

    rpc = RpcService()
    rpc.stream("/frames", frames)
    async with RpcTestClient(rpc, "/frames") as client:
        assert await client.next_frame() == b"one"
        while client.socket.closed is None:
            await asyncio.sleep(0)
        assert client.socket.closed == (RpcConnectionClose.NORMAL, "")
