import importlib
from collections.abc import AsyncIterator
from types import ModuleType
from typing import Any

import pytest
from pydantic import TypeAdapter


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


@pytest.fixture
def runtime(generated_client: ModuleType) -> ModuleType:
    return importlib.import_module(f"{generated_client.__name__}.internal")


async def test_client_core_wraps_response_validation_with_the_route(
    runtime: ModuleType,
) -> None:
    core = runtime.RpcClientCore(StubTransport("wrong"))
    route = runtime.RpcRouteInfo(
        method="math.count",
        result_adapter=TypeAdapter(int),
    )

    with pytest.raises(runtime.RpcResponseValidationError) as raised:
        await core.request(route)

    assert raised.value.method == "math.count"


async def test_client_core_closes_an_owned_transport_once(
    runtime: ModuleType,
) -> None:
    transport = StubTransport()
    core = runtime.RpcClientCore(transport)

    await core.close()
    await core.close()

    assert transport.closed == 1


def test_server_info_resolves_declared_variables(runtime: ModuleType) -> None:
    server = runtime.RpcServerInfo(
        name="control",
        url="wss://{host}/{sessionId}",
        variables={
            "host": runtime.RpcServerVariable("api.example.com"),
            "sessionId": runtime.RpcServerVariable("demo"),
        },
    )

    assert server.resolve({"sessionId": "s-123"}) == "wss://api.example.com/s-123"


def test_server_info_rejects_unknown_variables(runtime: ModuleType) -> None:
    server = runtime.RpcServerInfo(name="control", url="wss://example.com")

    with pytest.raises(ValueError, match="Unknown variables"):
        server.resolve({"token": "secret"})
