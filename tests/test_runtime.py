import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import pytest
from fastapi import Depends, FastAPI, WebSocket

import pyrpckit as rpc
from pyrpckit.fastapi import RpcAPIRouter


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


async def test_fastapi_router_resolves_connection_context_and_path_dependencies() -> (
    None
):
    router = RpcAPIRouter(prefix="/sessions/{session_id}")
    control = router.websocket("/control", name="control", namespace="browser")

    @router.connection()
    async def connection(
        websocket: WebSocket,
        session_id: str,
        token: str = Depends(_token),
    ) -> Connection:
        return Connection(f"{session_id}:{token}")

    @control.method()
    async def hello(connection: rpc.Inject[Connection]) -> str:
        return f"Hello, {connection.name}"

    route = router.routes[0]
    dependency = route.dependant.dependencies[0]
    assert dependency.call is connection
    assert [parameter.name for parameter in dependency.path_params] == ["session_id"]
    assert route.path == "/sessions/{session_id}/control"
    app = FastAPI()
    app.include_router(router)
    assert any(
        getattr(included, "original_router", None) is router
        or getattr(included, "path", None) == route.path
        for included in app.routes
    )

    websocket = FakeWebSocket(
        {
            "type": "websocket.receive",
            "text": '{"jsonrpc":"2.0","id":1,"method":"browser.hello"}',
        }
    )
    await route.endpoint(
        websocket=websocket,
        rpc_connection=Connection("abc:authenticated"),
    )
    assert websocket.sent == [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": "Hello, abc:authenticated",
        }
    ]


async def test_fastapi_channels_are_runtime_boundaries() -> None:
    router = RpcAPIRouter(prefix="/rpc")
    control = router.websocket("/control", name="control")
    screencast = router.websocket("/screencast", name="screencast")

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
    await router.routes[0].endpoint(websocket=websocket)

    assert websocket.sent[0]["error"] == {
        "code": -32601,
        "message": "Method not found",
    }


class SessionUpdated(rpc.RpcModel):
    revision: int


async def test_fastapi_runtime_starts_only_the_connected_channels_events() -> None:
    router = RpcAPIRouter()
    updates = router.websocket("/updates", name="updates", namespace="session")

    @updates.connection()
    async def connection() -> Connection:
        return Connection("Mathis")

    @updates.event("event", payload=SessionUpdated)
    async def session_events(
        connection: rpc.Inject[Connection],
    ) -> AsyncIterator[SessionUpdated]:
        yield SessionUpdated(revision=len(connection.name))

    websocket = FakeWebSocket()
    await router.routes[0].endpoint(
        websocket=websocket,
        rpc_connection=Connection("Mathis"),
    )

    assert websocket.sent == [
        {
            "jsonrpc": "2.0",
            "method": "session.event",
            "params": {"revision": 6},
        }
    ]


def test_fastapi_router_rejects_invalid_runtime_limits_and_channel_names() -> None:
    with pytest.raises(ValueError, match="max_concurrency"):
        RpcAPIRouter(max_concurrency=0)
    with pytest.raises(ValueError, match="max_queue_size"):
        RpcAPIRouter(max_queue_size=0)

    router = RpcAPIRouter()
    router.websocket("/one", name="duplicate")
    with pytest.raises(rpc.ProtocolDefinitionError, match="Duplicate.*duplicate"):
        router.websocket("/two", name="duplicate")
