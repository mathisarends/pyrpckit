import builtins
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pyrpckit import Inject, RpcChannel, RpcService
from pyrpckit.dishka import DishkaResolver, dishka_router

dishka = pytest.importorskip("dishka")
Scope = dishka.Scope


@dataclass(frozen=True)
class Connection:
    identifier: str


class Service:
    pass


class Container:
    def __init__(self, values: dict[type[object], object]) -> None:
        self.values = values
        self.calls: list[dict[str, Any]] = []
        self.child: Container | None = None
        self.exited = False

    async def get[DependencyT](self, dependency: type[DependencyT]) -> DependencyT:
        return self.values[dependency]  # type: ignore[return-value]

    @asynccontextmanager
    async def __call__(self, **kwargs: Any) -> AsyncGenerator["Container", None]:
        self.calls.append(kwargs)
        assert self.child is not None
        try:
            yield self.child
        finally:
            self.exited = True


async def test_dishka_resolver_maps_connection_and_call_scopes_to_containers() -> None:
    service = Service()
    request = Container({Service: service})
    session = Container({})
    session.child = request
    root = Container({})
    root.child = session
    connection = Connection("session-1")
    resolver = DishkaResolver(root)  # type: ignore[arg-type]

    async with resolver.enter_connection({Connection: connection}) as connected:
        assert isinstance(connected, DishkaResolver)
        async with connected.enter_scope() as scoped:
            assert await scoped.resolve(Service) is service
        assert session.exited
    assert root.exited

    assert root.calls == [{"context": {Connection: connection}, "scope": Scope.SESSION}]
    assert session.calls == [{}]


async def test_dishka_resolver_explains_when_the_optional_extra_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_import = builtins.__import__

    def missing_dishka(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "dishka":
            raise ImportError(name="dishka")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing_dishka)
    resolver = DishkaResolver(Container({}))  # type: ignore[arg-type]

    with pytest.raises(ModuleNotFoundError, match="dishka requires the 'dishka' extra"):
        async with resolver.enter_connection({}):
            pass


async def test_dishka_resolver_rejects_a_non_app_container() -> None:
    container = Container({})
    container.scope = Scope.SESSION
    resolver = DishkaResolver(container)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="needs the APP container.*SESSION"):
        async with resolver.enter_connection({}):
            pass


def test_dishka_router_reads_the_app_container_at_connection_time() -> None:
    expected = Service()
    request = Container({Service: expected})
    session = Container({})
    session.child = request
    root = Container({})
    root.child = session

    channel = RpcChannel("demo")

    @channel.method()
    async def resolve(service: Inject[Service]) -> bool:
        return service is expected

    rpc = RpcService()
    rpc.socket("/rpc", channels=(channel,))
    app = FastAPI()
    app.state.dishka_container = root
    app.include_router(dishka_router(rpc))

    with (
        TestClient(app) as client,
        client.websocket_connect("/rpc") as websocket,
    ):
        websocket.send_json({"jsonrpc": "2.0", "id": 1, "method": "demo.resolve"})
        assert websocket.receive_json()["result"] is True

    assert root.calls[0]["scope"] is Scope.SESSION
