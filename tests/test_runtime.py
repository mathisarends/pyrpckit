import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import pyrpckit as rpc
from pyrpckit.fastapi import RpcWebSocketApp


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
    router = rpc.RpcRouter()

    @router.method()
    async def value(scoped: rpc.Inject[ScopedValue]) -> int:
        await asyncio.sleep(0)
        return scoped.number

    app = rpc.RpcApp()
    app.include_router(router)
    resolver = ScopedResolver()
    response = await app.server(resolver=resolver).handle(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "value"},
            {"jsonrpc": "2.0", "id": 2, "method": "value"},
        ]
    )

    assert isinstance(response, list)
    assert {item.result for item in response} == {1, 2}
    assert resolver.entered == resolver.exited == 2


async def test_codec_returns_parse_errors_and_omits_batch_notifications() -> None:
    router = rpc.RpcRouter()

    @router.method()
    async def ping() -> str:
        return "pong"

    app = rpc.RpcApp()
    app.include_router(router)
    server = app.server()

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


class FakeWebSocket:
    def __init__(self) -> None:
        self.accepted = False
        self.sent: list[dict[str, Any]] = []
        self.received = False

    async def accept(self, subprotocol: str | None = None) -> None:
        self.accepted = True

    async def receive(self) -> dict[str, Any]:
        if not self.received:
            self.received = True
            return {
                "type": "websocket.receive",
                "text": '{"jsonrpc":"2.0","id":1,"method":"hello"}',
            }
        while not self.sent:
            await asyncio.sleep(0)
        return {"type": "websocket.disconnect"}

    async def send_text(self, data: str) -> None:
        self.sent.append(json.loads(data))


async def test_websocket_runtime_bridges_typed_connection_context() -> None:
    router = rpc.RpcRouter()

    @router.method()
    async def hello(connection: rpc.Inject[Connection]) -> str:
        return f"Hello, {connection.name}"

    app = rpc.RpcApp()
    app.include_router(router)
    websocket = FakeWebSocket()

    await RpcWebSocketApp(app).serve(
        websocket,
        context=Connection("Mathis"),
    )

    assert websocket.accepted
    assert websocket.sent == [{"jsonrpc": "2.0", "id": 1, "result": "Hello, Mathis"}]


class SessionUpdated(rpc.RpcModel):
    revision: int


class NotificationWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def accept(self, subprotocol: str | None = None) -> None:
        pass

    async def receive(self) -> dict[str, Any]:
        while not self.sent:
            await asyncio.sleep(0)
        return {"type": "websocket.disconnect"}

    async def send_text(self, data: str) -> None:
        self.sent.append(json.loads(data))


async def test_websocket_runtime_starts_decorated_notification_sources() -> None:
    router = rpc.RpcRouter(namespace="session")

    @router.notification("event", payload=SessionUpdated)
    async def session_notifications(
        connection: rpc.Inject[Connection],
    ) -> AsyncIterator[SessionUpdated]:
        yield SessionUpdated(revision=len(connection.name))

    app = rpc.RpcApp()
    app.include_router(router)
    websocket = NotificationWebSocket()

    await RpcWebSocketApp(app).serve(
        websocket,
        context=Connection("Mathis"),
    )

    assert websocket.sent == [
        {
            "jsonrpc": "2.0",
            "method": "session.event",
            "params": {"revision": 6},
        }
    ]
