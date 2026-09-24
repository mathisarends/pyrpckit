import asyncio
import json
from collections.abc import AsyncIterator

import pytest

from pyrpckit import (
    Inject,
    RpcChannel,
    RpcConnection,
    RpcConnectionClose,
    RpcErrorCode,
    RpcLimits,
    RpcModel,
    RpcRejection,
    RpcService,
)

from .testing import InMemorySocket, RpcTestClient


class Params(RpcModel):
    value: str


channel = RpcChannel("demo")
connections = []


class Observer:
    def __init__(self) -> None:
        self.closed = []

    async def request_started(self, context) -> None: ...

    async def request_finished(self, context) -> None: ...

    async def connection_closed(self, context) -> None:
        self.closed.append(context)


observer = Observer()


@channel.server.method()
async def echo(params: Params, connection: Inject[RpcConnection]) -> Params:
    connections.append(connection)
    return params


service = RpcService(observer=observer)
service.socket("/rpc", channels=(channel,))


async def test_request_and_connection_context() -> None:
    connections.clear()
    observer.closed.clear()
    async with RpcTestClient(service, "/rpc", headers={"Authorization": "x"}) as client:
        assert await client.request("demo.echo", {"value": "yes"}) == {"value": "yes"}
    assert connections[0].headers["authorization"] == "x"
    assert connections[0].closed
    assert connections[0].close_code == RpcConnectionClose.NORMAL
    assert connections[0].close_reason == ""
    assert observer.closed[0].connection is connections[0]
    assert observer.closed[0].close_code == RpcConnectionClose.NORMAL
    assert observer.closed[0].duration >= 0


async def test_client_close_information_is_exposed_on_the_connection() -> None:
    connections.clear()
    observer.closed.clear()
    async with RpcTestClient(service, "/rpc") as client:
        await client.request("demo.echo", {"value": "yes"})
        await client.socket.client_disconnect(1001, "Going away")

    assert connections[0].close_code == 1001
    assert connections[0].close_reason == "Going away"
    assert observer.closed[0].close_reason == "Going away"


async def test_binary_stream() -> None:
    streams = RpcChannel("streams")

    @streams.server.stream()
    async def frames() -> AsyncIterator[bytes]:
        yield b"one"

    rpc = RpcService()
    rpc.stream("/frames", frames)
    async with RpcTestClient(rpc, "/frames") as client:
        assert await client.next_frame() == b"one"
        while client.socket.closed is None:
            await asyncio.sleep(0)
        assert client.socket.closed == (RpcConnectionClose.NORMAL, "")


async def test_unsupported_subprotocol_is_rejected_before_acceptance() -> None:
    rpc = RpcService()
    rpc.socket("/rpc", channels=(channel,), subprotocol="rpc.v2")

    async with RpcTestClient(rpc, "/rpc", subprotocols=("rpc.v1",)) as client:
        await client.closed()

    assert not client.socket.accepted
    assert client.socket.rejection == (
        RpcRejection.PROTOCOL_ERROR,
        "Unsupported subprotocol",
    )


async def test_parse_error_is_answered_and_connection_remains_usable() -> None:
    socket = InMemorySocket("/rpc")
    task = asyncio.create_task(service.serve(socket))
    await asyncio.sleep(0)

    await socket.client_send("{")
    failure = json.loads(await socket.client_receive())
    await socket.client_send(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "demo.echo",
                "params": {"value": "still open"},
            }
        )
    )
    success = json.loads(await socket.client_receive())
    await socket.client_disconnect()
    await task

    assert failure["id"] is None
    assert failure["error"]["code"] == RpcErrorCode.PARSE_ERROR
    assert success == {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"value": "still open"},
    }


@pytest.mark.parametrize(
    ("frame", "expected"),
    [
        (b"\xff", (RpcConnectionClose.PROTOCOL_ERROR, "Invalid RPC frame")),
        ("12345", (RpcConnectionClose.MESSAGE_TOO_BIG, "")),
    ],
)
async def test_invalid_or_oversized_rpc_frames_close_the_connection(
    frame: str | bytes, expected: tuple[RpcConnectionClose, str]
) -> None:
    async with RpcTestClient(
        service, "/rpc", limits=RpcLimits(max_message_bytes=4)
    ) as client:
        await client.socket.client_send(frame)
        await client.closed()

    assert client.socket.closed == expected


async def test_server_events_are_sent_as_typed_notifications() -> None:
    events = RpcChannel("events")
    ready = asyncio.Event()

    @events.server.event(payload=Params)
    async def changed(trigger: Inject[asyncio.Event]) -> AsyncIterator[Params]:
        await trigger.wait()
        yield Params(value="ready")
        await asyncio.Event().wait()

    rpc = RpcService()
    rpc.socket("/events", channels=(events,))

    async with RpcTestClient(rpc, "/events", context=ready) as client:
        ready.set()
        notification = await client.next_notification()

    assert notification == ("events.changed", {"value": "ready"})


async def test_invalid_server_event_closes_with_internal_error() -> None:
    events = RpcChannel("events")

    @events.server.event(payload=Params)
    async def changed() -> AsyncIterator[Params]:
        yield {}  # type: ignore[misc]

    rpc = RpcService()
    rpc.socket("/events", channels=(events,))

    async with RpcTestClient(rpc, "/events") as client:
        await client.closed()

    assert client.socket.closed == (
        RpcConnectionClose.INTERNAL_ERROR,
        "Internal error",
    )


async def test_cancelling_a_connection_closes_it_as_shutdown() -> None:
    socket = InMemorySocket("/rpc")
    task = asyncio.create_task(service.serve(socket))
    await asyncio.sleep(0)
    assert socket.accepted

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert socket.closed == (RpcConnectionClose.SHUTDOWN, "")
