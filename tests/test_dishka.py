import builtins
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import pytest

from pyrpckit.dishka import DishkaResolver

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
