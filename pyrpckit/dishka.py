from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dishka import AsyncContainer

from pyrpckit.dependencies import RpcResolver


class DishkaResolver:
    """Adapt a Dishka container to connection and call-scoped RPC resolution."""

    def __init__(self, container: "AsyncContainer") -> None:
        self._container = container

    async def resolve[DependencyT](self, dependency: type[DependencyT]) -> DependencyT:
        return await self._container.get(dependency)

    @asynccontextmanager
    async def enter_connection(
        self,
        context: Mapping[type[Any], object],
    ) -> AsyncGenerator[RpcResolver, None]:
        try:
            from dishka import Scope
        except ImportError as error:
            raise ModuleNotFoundError(
                "pyrpckit.dishka requires the 'dishka' extra"
            ) from error
        async with self._container(
            context=dict(context),
            scope=Scope.SESSION,
        ) as session_container:
            yield DishkaResolver(session_container)

    @asynccontextmanager
    async def enter_scope(self) -> AsyncGenerator[RpcResolver, None]:
        async with self._container() as request_container:
            yield DishkaResolver(request_container)
