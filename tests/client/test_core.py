from collections.abc import AsyncIterator
from typing import Any

import pytest
from pydantic import TypeAdapter

from pyrpckit.client import RpcClientCore, RpcResponseValidationError, RpcRouteInfo


class StubTransport:
    def __init__(self, result: Any = None) -> None:
        self.result = result
        self.closed = 0

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        return self.result

    async def notifications(self) -> AsyncIterator[dict[str, Any]]:
        if False:
            yield {}

    async def close(self) -> None:
        self.closed += 1


async def test_client_core_wraps_response_validation_with_the_route() -> None:
    core = RpcClientCore(StubTransport("wrong"))

    with pytest.raises(RpcResponseValidationError) as raised:
        await core.request(
            RpcRouteInfo("math.count"),
            result_adapter=TypeAdapter(int),
        )

    assert raised.value.method == "math.count"


async def test_client_core_closes_an_owned_transport_once() -> None:
    transport = StubTransport()
    core = RpcClientCore(transport)

    await core.close()
    await core.close()

    assert transport.closed == 1
