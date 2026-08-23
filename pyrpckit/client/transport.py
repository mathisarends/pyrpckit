from collections.abc import AsyncIterator
from typing import Any, Protocol


class RpcTransport(Protocol):
    """The connection a generated client speaks through."""

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> Any: ...

    def notifications(self) -> AsyncIterator[dict[str, Any]]: ...

    async def close(self) -> None: ...
