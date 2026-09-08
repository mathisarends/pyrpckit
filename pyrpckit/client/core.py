from collections.abc import AsyncIterator, Iterable
from typing import Any, Protocol, TypeVar

from pydantic import TypeAdapter, ValidationError

from pyrpckit.client.errors import RpcResponseValidationError
from pyrpckit.client.metadata import JsonValue, RpcRouteInfo
from pyrpckit.client.transport import RpcTransport

ResultT = TypeVar("ResultT")


class RpcClientHook(Protocol):
    async def before_request(
        self,
        route: RpcRouteInfo,
        params: JsonValue,
    ) -> None: ...

    async def after_response(
        self,
        route: RpcRouteInfo,
        result: JsonValue,
    ) -> None: ...


class RpcClientCore:
    def __init__(
        self,
        transport: RpcTransport,
        *,
        close_transport: bool = True,
        hooks: Iterable[RpcClientHook] = (),
    ) -> None:
        self._transport = transport
        self._close_transport = close_transport
        self._hooks = tuple(hooks)
        self._closed = False

    async def request(
        self,
        route: RpcRouteInfo,
        *,
        params: dict[str, Any] | None = None,
        result_adapter: TypeAdapter[ResultT],
    ) -> ResultT:
        for hook in self._hooks:
            await hook.before_request(route, params)
        result = await self._transport.request(route.method, params)
        for hook in reversed(self._hooks):
            await hook.after_response(route, result)
        try:
            return result_adapter.validate_python(result)
        except ValidationError as error:
            raise RpcResponseValidationError(route.method, error) from error

    def notifications(self) -> AsyncIterator[dict[str, Any]]:
        return self._transport.notifications()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._close_transport:
            await self._transport.close()


class UnsetType:
    __slots__ = ()

    def __repr__(self) -> str:
        return "UNSET"


UNSET = UnsetType()
