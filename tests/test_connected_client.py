import asyncio
import json
from contextlib import suppress
from typing import Any

import pytest

from pyrpckit import (
    Inject,
    RpcChannel,
    RpcClientClosedError,
    RpcClientMethodFailedError,
    RpcClientMethodResultError,
    RpcClientMethodTimeoutError,
    RpcConnectedClient,
    RpcLimits,
    RpcService,
)

from .conftest import (
    HelloParams,
    MediaPlayParams,
    MediaPlayResult,
    MediaUnavailableError,
    TestResolver,
    media_play,
    room_channel,
    room_ping,
)
from .testing import InMemorySocket


class Rooms:
    def __init__(self) -> None:
        self.clients: dict[str, RpcConnectedClient] = {}
        self.attached = asyncio.Event()


hello_channel = RpcChannel("hello")


@hello_channel.server.method("attach")
async def attach(
    params: HelloParams,
    client: Inject[RpcConnectedClient],
    rooms: Inject[Rooms],
) -> None:
    rooms.clients[params.room_id] = client
    rooms.attached.set()


@hello_channel.server.method("relay")
async def relay(client: Inject[RpcConnectedClient]) -> bool:
    result = await client.call(media_play, MediaPlayParams(media_uri="spotify:relay"))
    return result.started


service = RpcService()
service.socket("/rooms", channels=(room_channel, hello_channel), name="rooms")
other_channel = RpcChannel("other")
other_play = other_channel.client.method("play")
service.socket("/other", channels=(other_channel,), name="other")


class Session:
    def __init__(self, limits: RpcLimits | None = None) -> None:
        self.rooms = Rooms()
        self.socket = InMemorySocket("/rooms")
        self.limits = limits
        self.task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> "Session":
        self.task = asyncio.create_task(
            service.serve(
                self.socket, resolver=TestResolver(self.rooms), limits=self.limits
            )
        )
        await self.send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "hello.attach",
                "params": {"roomId": "kitchen"},
            }
        )
        assert await self.receive() == {"jsonrpc": "2.0", "id": 1, "result": None}
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.socket.client_disconnect()
        assert self.task is not None
        with suppress(asyncio.CancelledError):
            await self.task

    @property
    def client(self) -> RpcConnectedClient:
        return self.rooms.clients["kitchen"]

    async def send(self, message: dict[str, Any]) -> None:
        await self.socket.client_send(json.dumps(message))

    async def receive(self) -> dict[str, Any]:
        return json.loads(await self.socket.client_receive())


async def test_a_client_method_is_sent_as_a_request_and_returns_the_typed_result() -> (
    None
):
    async with Session() as session:
        call = asyncio.create_task(
            session.client.call(media_play, MediaPlayParams(media_uri="spotify:1"))
        )
        request = await session.receive()
        await session.send(
            {"jsonrpc": "2.0", "id": request["id"], "result": {"started": True}}
        )

        result = await call

    assert request == {
        "jsonrpc": "2.0",
        "id": "server:1",
        "method": "room.media.play",
        "params": {"mediaUri": "spotify:1"},
    }
    assert isinstance(result, MediaPlayResult)
    assert result.started is True


async def test_a_client_method_without_params_omits_them_and_returns_none() -> None:
    async with Session() as session:
        call = asyncio.create_task(session.client.call(room_ping))
        request = await session.receive()
        await session.send({"jsonrpc": "2.0", "id": request["id"], "result": None})

        assert await call is None

    assert "params" not in request


async def test_a_declared_error_is_raised_as_its_typed_exception() -> None:
    async with Session() as session:
        call = asyncio.create_task(
            session.client.call(media_play, MediaPlayParams(media_uri="spotify:1"))
        )
        request = await session.receive()
        await session.send(
            {
                "jsonrpc": "2.0",
                "id": request["id"],
                "error": {
                    "code": -32010,
                    "message": "Speaker offline",
                    "data": {
                        "code": "media_unavailable",
                        "details": {"speaker_id": "s1"},
                    },
                },
            }
        )

        with pytest.raises(MediaUnavailableError) as error:
            await call

    assert error.value.message == "Speaker offline"
    assert error.value.details.speaker_id == "s1"


async def test_an_undeclared_error_is_raised_as_a_remote_error() -> None:
    async with Session() as session:
        call = asyncio.create_task(session.client.call(room_ping))
        request = await session.receive()
        await session.send(
            {
                "jsonrpc": "2.0",
                "id": request["id"],
                "error": {
                    "code": -32601,
                    "message": "Method not found",
                    "data": {"code": "method_not_found"},
                },
            }
        )

        with pytest.raises(RpcClientMethodFailedError) as error:
            await call

    assert error.value.rpc_code == -32601
    assert error.value.code == "method_not_found"


async def test_an_invalid_result_raises_a_result_error() -> None:
    async with Session() as session:
        call = asyncio.create_task(
            session.client.call(media_play, MediaPlayParams(media_uri="spotify:1"))
        )
        request = await session.receive()
        await session.send({"jsonrpc": "2.0", "id": request["id"], "result": {}})

        with pytest.raises(RpcClientMethodResultError):
            await call


async def test_a_timeout_raises_and_drops_the_late_response() -> None:
    async with Session() as session:
        with pytest.raises(RpcClientMethodTimeoutError):
            await session.client.call(room_ping, timeout=0.01)
        request = await session.receive()
        await session.send({"jsonrpc": "2.0", "id": request["id"], "result": None})
        await session.send({"jsonrpc": "2.0", "id": 2, "method": "hello.relay"})

        client_method = await session.receive()
        await session.send(
            {"jsonrpc": "2.0", "id": client_method["id"], "result": {"started": False}}
        )

        assert await session.receive() == {"jsonrpc": "2.0", "id": 2, "result": False}


async def test_client_method_uses_limit_timeout_unless_disabled() -> None:
    async with Session(RpcLimits(client_method_timeout=0.01)) as session:
        with pytest.raises(RpcClientMethodTimeoutError) as error:
            await session.client.call(room_ping)
        assert error.value.timeout == 0.01
        await session.receive()

        call = asyncio.create_task(session.client.call(room_ping, timeout=None))
        request = await session.receive()
        await asyncio.sleep(0.02)
        assert not call.done()
        await session.send({"jsonrpc": "2.0", "id": request["id"], "result": None})
        assert await call is None


async def test_pending_calls_fail_when_the_connection_closes() -> None:
    session = Session()
    await session.__aenter__()
    client = session.client
    call = asyncio.create_task(client.call(room_ping))
    await session.receive()

    await session.__aexit__()

    with pytest.raises(RpcClientClosedError):
        await call
    assert client.closed
    with pytest.raises(RpcClientClosedError):
        await client.call(room_ping)


async def test_a_method_handler_can_await_a_client_method() -> None:
    async with Session() as session:
        await session.send({"jsonrpc": "2.0", "id": 2, "method": "hello.relay"})
        client_method = await session.receive()
        await session.send(
            {"jsonrpc": "2.0", "id": client_method["id"], "result": {"started": True}}
        )

        assert await session.receive() == {"jsonrpc": "2.0", "id": 2, "result": True}

    assert client_method["params"] == {"mediaUri": "spotify:relay"}


async def test_client_methods_of_other_endpoints_are_rejected() -> None:
    async with Session() as session:
        with pytest.raises(ValueError, match="not declared on endpoint"):
            await session.client.call(other_play)


async def test_params_must_match_the_declaration() -> None:
    async with Session() as session:
        with pytest.raises(TypeError, match="takes no params"):
            await session.client.call(room_ping, MediaPlayParams(media_uri="x"))
        with pytest.raises(TypeError, match="needs params"):
            await session.client.call(media_play)


async def test_outgoing_requests_respect_the_message_size_limit() -> None:
    async with Session(RpcLimits(max_message_bytes=256)) as session:
        with pytest.raises(ValueError, match="max_message_bytes"):
            await session.client.call(
                media_play, MediaPlayParams(media_uri="spotify:" + "x" * 256)
            )


async def test_outgoing_requests_respect_the_concurrency_limit() -> None:
    async with Session(RpcLimits(max_concurrency=1)) as session:
        first = asyncio.create_task(session.client.call(room_ping))
        second = asyncio.create_task(session.client.call(room_ping))
        request = await session.receive()
        await asyncio.sleep(0.01)

        assert session.socket._outgoing.empty()
        await session.send({"jsonrpc": "2.0", "id": request["id"], "result": None})
        following = await session.receive()
        await session.send({"jsonrpc": "2.0", "id": following["id"], "result": None})

        assert await first is None
        assert await second is None


async def test_responses_to_unknown_ids_are_dropped_without_a_reply() -> None:
    async with Session() as session:
        await session.send({"jsonrpc": "2.0", "id": "server:99", "result": None})
        await session.send({"jsonrpc": "2.0", "id": 2, "method": "hello.relay"})

        client_method = await session.receive()

    assert client_method["method"] == "room.media.play"
