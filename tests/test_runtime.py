import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from threading import Event
from typing import Annotated

import pytest
from fastapi import APIRouter, Depends, FastAPI, WebSocket, WebSocketException
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import pyrpckit as rpc
from pyrpckit.fastapi import serve


@dataclass(frozen=True)
class ScopedValue:
    number: int


class ScopedResolver:
    def __init__(self) -> None:
        self.entered = 0
        self.exited = 0

    @asynccontextmanager
    async def enter_scope(self) -> AsyncGenerator[rpc.RpcResolver, None]:
        self.entered += 1
        value = ScopedValue(self.entered)
        try:
            yield ValueResolver(value)
        finally:
            self.exited += 1

    async def resolve[DependencyT](
        self,
        dependency: type[DependencyT],
    ) -> DependencyT:
        raise AssertionError("Dependencies must be resolved inside a call scope")


class ValueResolver:
    def __init__(self, value: object) -> None:
        self.value = value

    async def resolve[DependencyT](
        self,
        dependency: type[DependencyT],
    ) -> DependencyT:
        assert isinstance(self.value, dependency)
        return self.value  # type: ignore[return-value]


async def test_batch_members_receive_independent_call_scopes() -> None:
    channel = rpc.RpcChannel()

    @channel.method()
    async def value(scoped: rpc.Inject[ScopedValue]) -> int:
        await asyncio.sleep(0)
        return scoped.number

    resolver = ScopedResolver()
    response = await channel.server(resolver=resolver).handle(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "value"},
            {"jsonrpc": "2.0", "id": 2, "method": "value"},
        ]
    )

    assert isinstance(response, list)
    assert {item.result for item in response} == {1, 2}
    assert resolver.entered == resolver.exited == 2


async def test_codec_returns_parse_errors_and_omits_batch_notifications() -> None:
    channel = rpc.RpcChannel()

    @channel.method()
    async def ping() -> str:
        return "pong"

    server = channel.server()
    parse_error = json.loads(await server.handle_json("{") or "")
    batch = json.loads(
        await server.handle_json(
            '[{"jsonrpc":"2.0","method":"ping"},'
            '{"jsonrpc":"2.0","id":2,"method":"ping"}]'
        )
        or ""
    )

    assert parse_error["error"]["code"] == rpc.RpcErrorCode.PARSE_ERROR
    assert batch == [{"jsonrpc": "2.0", "id": 2, "result": "pong"}]


@dataclass(frozen=True)
class Connection:
    name: str


def _token() -> str:
    return "authenticated"


class FakeWebSocket:
    def __init__(self, *messages: dict[str, object]) -> None:
        self.messages = list(messages)
        self.sent: list[dict[str, object]] = []
        self.accepted = False
        self.subprotocol: str | None = None

    async def accept(self, subprotocol: str | None = None) -> None:
        self.accepted = True
        self.subprotocol = subprotocol

    async def receive(self) -> dict[str, object]:
        if self.messages:
            return self.messages.pop(0)
        while not self.sent:
            await asyncio.sleep(0)
        return {"type": "websocket.disconnect"}

    async def send_text(self, data: str) -> None:
        self.sent.append(json.loads(data))


def test_fastapi_endpoint_resolves_dependencies_and_cleans_up() -> None:
    router = APIRouter(prefix="/sessions/{session_id}")
    control = rpc.RpcChannel(name="control", namespace="browser")
    lifecycle: list[str] = []
    cleaned_up = Event()

    async def connection(
        websocket: WebSocket,
        session_id: str,
        token: str = Depends(_token),
    ) -> AsyncIterator[Connection]:
        lifecycle.append("entered")
        try:
            yield Connection(f"{session_id}:{token}")
        finally:
            lifecycle.append("exited")
            cleaned_up.set()

    @control.method()
    async def hello(
        connection: rpc.Inject[Connection],
        websocket: rpc.Inject[WebSocket],
    ) -> str:
        assert websocket.path_params["session_id"] == "abc"
        return f"Hello, {connection.name}"

    @router.websocket("/control")
    async def endpoint(
        websocket: WebSocket,
        context: Annotated[Connection, Depends(connection)],
    ) -> None:
        await serve(control, websocket, context=context, subprotocol="jsonrpc")

    parent = APIRouter(prefix="/v1")
    parent.include_router(router)
    app = FastAPI()
    app.include_router(parent, prefix="/api")
    app.dependency_overrides[_token] = lambda: "overridden"

    with TestClient(app) as client:
        with client.websocket_connect(
            "/api/v1/sessions/abc/control", subprotocols=["jsonrpc"]
        ) as websocket:
            assert websocket.accepted_subprotocol == "jsonrpc"
            websocket.send_json({"jsonrpc": "2.0", "id": 1, "method": "browser.hello"})
            assert websocket.receive_json() == {
                "jsonrpc": "2.0",
                "id": 1,
                "result": "Hello, abc:overridden",
            }
            assert lifecycle == ["entered"]
        assert cleaned_up.wait(timeout=5)
        assert lifecycle == ["entered", "exited"]


def test_fastapi_auth_can_reject_before_serve() -> None:
    app = FastAPI()
    channel = rpc.RpcChannel()
    served = False

    async def authorize() -> None:
        raise WebSocketException(code=1008)

    @app.websocket("/rpc", dependencies=[Depends(authorize)])
    async def endpoint(websocket: WebSocket) -> None:
        nonlocal served
        served = True
        await serve(channel, websocket)

    with (
        TestClient(app) as client,
        pytest.raises(WebSocketDisconnect) as error,
        client.websocket_connect("/rpc"),
    ):
        pytest.fail("Unauthorized connection accepted")
    assert error.value.code == 1008
    assert not served


async def test_fastapi_channels_are_runtime_boundaries() -> None:
    control = rpc.RpcChannel(name="control")
    screencast = rpc.RpcChannel(name="screencast")

    @control.method()
    async def key_down() -> str:
        return "key"

    @screencast.method()
    async def frame() -> str:
        return "frame"

    websocket = FakeWebSocket(
        {
            "type": "websocket.receive",
            "text": '{"jsonrpc":"2.0","id":1,"method":"frame"}',
        }
    )
    await serve(control, websocket)

    assert websocket.sent[0]["error"] == {
        "code": -32601,
        "message": "Method not found",
    }


class SessionUpdated(rpc.RpcModel):
    revision: int


def test_fastapi_events_are_scoped_and_stopped_on_disconnect() -> None:
    app = FastAPI()
    updates = rpc.RpcChannel(name="updates", namespace="session")
    other = rpc.RpcChannel(name="other")
    lifecycle: list[str] = []
    stopped = Event()

    @updates.event("event", payload=SessionUpdated)
    async def events() -> AsyncIterator[SessionUpdated]:
        lifecycle.append("started")
        try:
            yield SessionUpdated(revision=1)
            await asyncio.Event().wait()
        finally:
            lifecycle.append("stopped")
            stopped.set()

    @other.event("unused", payload=SessionUpdated)
    async def unused() -> AsyncIterator[SessionUpdated]:
        pytest.fail("Unconnected channel event source started")
        yield SessionUpdated(revision=0)

    @app.websocket("/updates")
    async def endpoint(websocket: WebSocket) -> None:
        await serve(updates, websocket)

    @app.websocket("/other")
    async def other_endpoint(websocket: WebSocket) -> None:
        await serve(other, websocket)

    with TestClient(app) as client:
        with client.websocket_connect("/updates") as websocket:
            assert websocket.receive_json() == {
                "jsonrpc": "2.0",
                "method": "session.event",
                "params": {"revision": 1},
            }
            assert lifecycle == ["started"]
        assert stopped.wait(timeout=5)
        assert lifecycle == ["started", "stopped"]


async def test_fastapi_runtime_starts_only_the_connected_channels_events() -> None:
    updates = rpc.RpcChannel(name="updates", namespace="session")

    @updates.event("event", payload=SessionUpdated)
    async def session_events(
        connection: rpc.Inject[Connection],
    ) -> AsyncIterator[SessionUpdated]:
        yield SessionUpdated(revision=len(connection.name))

    websocket = FakeWebSocket()
    await serve(updates, websocket, context={Connection: Connection("Mathis")})

    assert websocket.sent == [
        {
            "jsonrpc": "2.0",
            "method": "session.event",
            "params": {"revision": 6},
        }
    ]


async def test_serve_rejects_invalid_limits_before_accept() -> None:
    channel = rpc.RpcChannel()
    websocket = FakeWebSocket()
    with pytest.raises(ValueError, match="max_concurrency"):
        await serve(channel, websocket, max_concurrency=0)
    with pytest.raises(ValueError, match="max_queue_size"):
        await serve(channel, websocket, max_queue_size=0)
    assert not websocket.accepted
