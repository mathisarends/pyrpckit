import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, nullcontext
from contextvars import ContextVar

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketDenialResponse

from rpckit import (
    RpcChannel,
    RpcConnectionScope,
    RpcMiddleware,
    RpcObserver,
    RpcRejection,
    RpcRequestScope,
    RpcService,
    RpcStreamScope,
)
from rpckit.fastapi import RpcRoutes, create_router
from rpckit.testing import InMemorySocket, RpcTestClient

request_id: ContextVar[object] = ContextVar("test.request", default=None)
connection_name: ContextVar[str | None] = ContextVar("test.connection", default=None)


class Recorder(RpcMiddleware):
    def __init__(self, name="recorder", order=None):
        self.name = name
        self.order = [] if order is None else order
        self.connections: list[RpcConnectionScope] = []
        self.requests: list[RpcRequestScope] = []
        self.streams: list[RpcStreamScope] = []
        self.frames: list[tuple[str, int]] = []

    @asynccontextmanager
    async def connection(self, scope):
        self.connections.append(scope)
        token = connection_name.set(scope.endpoint.name)
        try:
            yield
        finally:
            connection_name.reset(token)

    @asynccontextmanager
    async def request(self, scope):
        self.requests.append(scope)
        self.order.append(f"enter:{self.name}")
        token = request_id.set(scope.request.request_id)
        try:
            yield
        finally:
            self.order.append(f"exit:{self.name}")
            assert scope.outcome is not None
            assert request_id.get() == scope.request.request_id
            request_id.reset(token)

    @asynccontextmanager
    async def stream(self, scope):
        self.streams.append(scope)
        yield

    async def stream_frame_sent(self, connection, size):
        self.frames.append(("sent", size))

    async def stream_frame_received(self, connection, size):
        self.frames.append(("received", size))


async def test_batch_scopes_are_isolated_and_ordered():
    order = []
    outer = Recorder("outer", order)
    inner = Recorder("inner", order)
    channel = RpcChannel("batch")
    entered = 0
    both = asyncio.Event()

    @channel.method()
    async def identify() -> int:
        nonlocal entered
        entered += 1
        if entered == 2:
            both.set()
        await both.wait()
        return request_id.get()

    server = channel.create_server(middleware=[outer, inner])
    result = await server.handle(
        [{"jsonrpc": "2.0", "id": i, "method": "batch.identify"} for i in (7, 8)]
    )
    assert [item.result for item in result] == [7, 8]
    assert request_id.get() is None
    assert len(outer.requests) == len(inner.requests) == 2
    assert order == [
        "enter:outer",
        "enter:inner",
        "enter:outer",
        "enter:inner",
        "exit:inner",
        "exit:outer",
        "exit:inner",
        "exit:outer",
    ]


async def test_service_endpoint_and_serving_middleware_compose_once():
    order = []
    adapter, service, endpoint = [
        Recorder(name, order) for name in ("adapter", "service", "endpoint")
    ]
    channel = RpcChannel("demo")

    @channel.method()
    async def ping() -> str:
        assert connection_name.get() == "rpc"
        return "pong"

    rpc = RpcService(middleware=[service])
    mounted = rpc.socket("/rpc", channels=[channel], middleware=[endpoint])
    socket = InMemorySocket("/rpc")
    task = asyncio.create_task(mounted.serve(socket, middleware=[adapter]))
    await socket.client_send('{"jsonrpc":"2.0","id":1,"method":"demo.ping"}')
    assert "pong" in await socket.client_receive()
    await socket.client_disconnect()
    await task
    assert order == [
        "enter:adapter",
        "enter:service",
        "enter:endpoint",
        "exit:endpoint",
        "exit:service",
        "exit:adapter",
    ]
    assert all(len(item.connections) == 1 for item in (adapter, service, endpoint))


async def test_notifications_invalid_json_and_invalid_batches_have_outcomes():
    recorder = Recorder()
    channel = RpcChannel("demo")

    @channel.method()
    async def broken() -> None:
        raise ValueError("private failure")

    server = channel.create_server(middleware=[recorder])
    assert await server.handle({"jsonrpc": "2.0", "method": "demo.broken"}) is None
    await server.handle_json("{")
    await server.handle([])
    assert len(recorder.requests) == 3
    assert recorder.requests[0].request.notification
    assert isinstance(recorder.requests[0].outcome.error, ValueError)
    assert recorder.requests[0].outcome.error_code == "-32603"
    assert all(scope.outcome.error is not None for scope in recorder.requests)


async def test_request_cancellation_closes_observer_and_middleware():
    started = asyncio.Event()
    recorder = Recorder()
    finished = []

    class Observer(RpcObserver):
        async def request_finished(self, context):
            finished.append(context)

    channel = RpcChannel("demo")

    @channel.method()
    async def wait() -> None:
        started.set()
        await asyncio.Event().wait()

    server = channel.create_server(middleware=[recorder], observer=Observer())
    task = asyncio.create_task(
        server.handle({"jsonrpc": "2.0", "id": 1, "method": "demo.wait"})
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(finished) == 1
    assert isinstance(finished[0].error, asyncio.CancelledError)
    assert recorder.requests[0].outcome.cancelled


@pytest.mark.parametrize("cancel", [False, True])
async def test_stream_cleanup_and_cancellation(cancel):
    recorder = Recorder()
    released = []
    channel = RpcChannel("media")

    @channel.stream()
    async def frames() -> AsyncIterator[bytes]:
        try:
            assert connection_name.get() == "media"
            yield b"one"
            await asyncio.Event().wait()
        finally:
            released.append(True)

    rpc = RpcService(middleware=[recorder])
    endpoint = rpc.stream("/media", frames)
    socket = InMemorySocket("/media")
    task = asyncio.create_task(endpoint.serve(socket))
    assert await socket.client_receive() == b"one"
    if cancel:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        await socket.client_disconnect()
        await task
    assert released == [True]
    assert recorder.frames == [("sent", 3)]
    assert recorder.streams[0].outcome.cancelled is cancel
    assert recorder.connections[0].outcome.cancelled is cancel


async def test_handshake_rejection_is_observed():
    recorder = Recorder()
    channel = RpcChannel("media")

    @channel.stream()
    async def frames() -> AsyncIterator[bytes]:
        yield b"unused"

    rpc = RpcService(middleware=[recorder])
    endpoint = rpc.stream("/media", frames, subprotocol="required")
    await endpoint.serve(InMemorySocket("/media"))
    assert not recorder.connections[0].outcome.accepted
    assert recorder.connections[0].outcome.rejection is RpcRejection.PROTOCOL_ERROR
    assert not recorder.streams


async def test_middleware_failures_cannot_replace_a_response(caplog):
    class Broken(RpcMiddleware):
        @asynccontextmanager
        async def request(self, scope):
            if scope.request.request_id == 1:
                raise RuntimeError("enter failed")
            yield
            raise RuntimeError("exit failed")

    channel = RpcChannel("demo")

    @channel.method()
    async def ping() -> str:
        return "pong"

    server = channel.create_server(middleware=[Broken()])
    for i in (1, 2):
        assert (
            await server.handle({"jsonrpc": "2.0", "id": i, "method": "demo.ping"})
        ).result == "pong"
    assert "enter failed" in caplog.text and "exit failed" in caplog.text


def test_fastapi_observes_context_rejections_and_does_not_enter_twice():
    recorder = Recorder()
    channel = RpcChannel("demo")

    @channel.method()
    async def ping() -> str:
        return "pong"

    rpc = RpcService()
    endpoint = rpc.socket("/rpc", channels=[channel], context=str)

    async def authenticate() -> str:
        assert connection_name.get() == "rpc"
        raise ValueError("Access denied")

    router = APIRouter()
    RpcRoutes(
        router,
        context=authenticate,
        middleware=[recorder],
        rejections={ValueError: RpcRejection.FORBIDDEN},
    ).mount(endpoint)
    web = FastAPI()
    web.include_router(router)
    with (
        TestClient(web) as client,
        pytest.raises(WebSocketDenialResponse),
        client.websocket_connect("/rpc"),
    ):
        pass
    assert len(recorder.connections) == 1
    outcome = recorder.connections[0].outcome
    assert not outcome.accepted
    assert outcome.rejection is RpcRejection.FORBIDDEN
    assert isinstance(outcome.error, ValueError)

    second = Recorder()
    other = RpcService(middleware=[second])
    other.socket("/rpc", channels=[channel])
    app = FastAPI()
    app.include_router(create_router(other))
    with TestClient(app) as client, client.websocket_connect("/rpc") as socket:
        socket.send_json({"jsonrpc": "2.0", "id": 1, "method": "demo.ping"})
        assert socket.receive_json()["result"] == "pong"
    assert len(second.connections) == 1 and len(second.requests) == 1


async def test_structural_middleware_needs_no_observer_methods():
    class Structural:
        def connection(self, scope):
            return nullcontext()

        def request(self, scope):
            return nullcontext()

        def stream(self, scope):
            return nullcontext()

    channel = RpcChannel("demo")

    @channel.method()
    async def ping() -> str:
        return "pong"

    rpc = RpcService(middleware=[Structural()])
    rpc.socket("/rpc", channels=[channel])
    async with RpcTestClient(rpc, "/rpc") as client:
        assert await client.request("demo.ping") == "pong"


async def test_partial_middleware_entry_cancellation_unwinds_entered_scopes():
    recorder = Recorder()

    class Cancel(RpcMiddleware):
        @asynccontextmanager
        async def request(self, scope):
            raise asyncio.CancelledError()
            yield

    channel = RpcChannel("demo")

    @channel.method()
    async def ping() -> str:
        raise AssertionError("handler must not run")

    server = channel.create_server(middleware=[recorder, Cancel()])
    with pytest.raises(asyncio.CancelledError):
        await server.handle({"jsonrpc": "2.0", "id": 1, "method": "demo.ping"})
    assert recorder.requests[0].outcome.cancelled
    assert recorder.order == ["enter:recorder", "exit:recorder"]


async def test_stream_generator_and_di_cleanup_errors_are_observed():
    recorder = Recorder()
    cleanup = []

    class Resolver:
        async def resolve(self, dependency):
            raise LookupError(dependency)

        @asynccontextmanager
        async def enter_scope(self):
            try:
                yield self
            finally:
                cleanup.append("di")
                raise ValueError("DI cleanup failed")

    channel = RpcChannel("media")

    @channel.stream()
    async def frames() -> AsyncIterator[bytes]:
        try:
            yield b"one"
            await asyncio.Event().wait()
        finally:
            cleanup.append("generator")

    rpc = RpcService(middleware=[recorder])
    endpoint = rpc.stream("/media", frames)
    socket = InMemorySocket("/media")
    task = asyncio.create_task(endpoint.serve(socket, resolver=Resolver()))
    assert await socket.client_receive() == b"one"
    await socket.client_disconnect()
    await task
    assert cleanup == ["generator", "di"]
    assert isinstance(recorder.streams[0].outcome.error, ValueError)
    assert isinstance(recorder.connections[0].outcome.error, ValueError)
