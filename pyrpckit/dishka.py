from __future__ import annotations

from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dishka import AsyncContainer
    from fastapi import APIRouter

    from pyrpckit.service import RpcService

from pyrpckit.dependencies import RpcResolver


class DishkaResolver:
    """Adapt a Dishka container to connection and call-scoped RPC resolution."""

    def __init__(self, container: AsyncContainer) -> None:
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
        container_scope = getattr(self._container, "scope", None)
        if container_scope is not None and container_scope is not Scope.APP:
            raise ValueError(
                "DishkaResolver needs the APP container "
                "(app.state.dishka_container), got a "
                f"{container_scope.name} container. pyrpckit opens the SESSION "
                "scope per connection itself."
            )
        async with self._container(
            context=dict(context),
            scope=Scope.SESSION,
        ) as session_container:
            yield DishkaResolver(session_container)

    @asynccontextmanager
    async def enter_scope(self) -> AsyncGenerator[RpcResolver, None]:
        async with self._container() as request_container:
            yield DishkaResolver(request_container)


def dishka_router(service: RpcService, **options: Any) -> APIRouter:
    """Serve a service with the app's root Dishka container."""
    try:
        from pyrpckit.fastapi import create_router
    except ImportError as error:
        raise ModuleNotFoundError(
            "dishka_router requires the 'fastapi' extra"
        ) from error

    return create_router(
        service,
        resolver_factory=lambda websocket: DishkaResolver(
            websocket.app.state.dishka_container
        ),
        **options,
    )
