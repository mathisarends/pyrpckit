import asyncio
from collections.abc import AsyncIterator

import pytest

from pyrpckit import (
    ConnectionRejected,
    Inject,
    RpcChannel,
    RpcConnection,
    RpcConnectionClose,
    RpcModel,
    RpcRejection,
    RpcService,
)
from pyrpckit.testing import RpcTestClient, RpcTestConnectionClosed


class Params(RpcModel):
    value: str


class User:
    pass


async def authenticate(connection: RpcConnection) -> User:
    if "authorization" not in connection.headers:
        raise ConnectionRejected(RpcRejection.UNAUTHORIZED)
    return User()


channel = RpcChannel("demo")


@channel.method()
async def echo(params: Params, user: Inject[User]) -> Params:
    return params


service = RpcService(connect=authenticate)
service.socket("/rpc", channel)


async def test_request_and_hook_context() -> None:
    async with RpcTestClient(service, "/rpc", headers={"Authorization": "x"}) as client:
        assert await client.request("demo.echo", {"value": "yes"}) == {"value": "yes"}


async def test_rejection_does_not_accept() -> None:
    async with RpcTestClient(service, "/rpc") as client:
        with pytest.raises(RpcTestConnectionClosed):
            await client.request("demo.echo", {"value": "yes"})
        assert client.socket.rejection == (RpcRejection.UNAUTHORIZED, "Unauthorized")
        assert not client.socket.accepted


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
