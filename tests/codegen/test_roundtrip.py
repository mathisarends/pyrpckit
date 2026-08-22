import asyncio
from collections.abc import AsyncIterator
from types import ModuleType
from typing import Any

import pytest

from pyrpckit import RpcError, RpcProtocol, RpcServer
from pyrpckit.client import RpcRemoteError
from tests.conftest import (
    GREETING_FEATURE,
    GreetingRpcMethods,
    UnknownGreetingError,
)


class LoopbackTransport:
    """Carries requests straight into an RpcServer, over JSON like the wire."""

    def __init__(self, server: RpcServer) -> None:
        self._server = server
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._next_id = 0
        self.closed = False

    async def request(self, method: str, params: dict[str, Any]) -> Any:
        self._next_id += 1
        response = await self._server.handle(
            {"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params}
        )
        assert response is not None
        payload = response.model_dump(mode="json")
        if "error" in payload:
            raise RpcRemoteError(payload["error"]["code"], payload["error"]["message"])
        return payload["result"]

    def publish(self, message: dict[str, Any]) -> None:
        self._queue.put_nowait(message)

    async def notifications(self) -> AsyncIterator[dict[str, Any]]:
        while (message := await self._queue.get()) is not None:
            yield message

    async def close(self) -> None:
        self.closed = True
        self._queue.put_nowait(None)


def _to_rpc_error(error: Exception) -> RpcError | None:
    if isinstance(error, UnknownGreetingError):
        return RpcError(-32004, f"Unknown greeting: {error}")
    return None


@pytest.fixture
def handler() -> GreetingRpcMethods:
    return GreetingRpcMethods()


@pytest.fixture
def transport(handler: GreetingRpcMethods) -> LoopbackTransport:
    server = RpcServer(
        RpcProtocol((GREETING_FEATURE,)),
        (handler,),
        error_mapper=_to_rpc_error,
    )
    return LoopbackTransport(server)


async def test_a_generated_call_reaches_the_handler_and_returns_a_model(
    generated_client: ModuleType,
    transport: LoopbackTransport,
    handler: GreetingRpcMethods,
) -> None:
    client = generated_client.GreetingClient(transport)

    result = await client.greeting.say(name="Mathis")

    assert result.text == "Hello, Mathis!"
    assert type(result).__name__ == "SayResult"
    assert handler.greeted == ["Mathis"]


async def test_a_call_without_a_result_returns_none(
    generated_client: ModuleType,
    transport: LoopbackTransport,
    handler: GreetingRpcMethods,
) -> None:
    client = generated_client.GreetingClient(transport)
    await client.greeting.say(name="Mathis")

    assert await client.greeting.forget(name="Mathis") is None
    assert handler.greeted == []


async def test_a_server_failure_surfaces_as_a_remote_error(
    generated_client: ModuleType,
    transport: LoopbackTransport,
) -> None:
    client = generated_client.GreetingClient(transport)

    with pytest.raises(RpcRemoteError) as error:
        await client.greeting.forget(name="nobody")

    assert error.value.code == -32004
    assert error.value.message == "Unknown greeting: nobody"


async def test_notifications_are_parsed_into_their_event_union(
    generated_client: ModuleType,
    transport: LoopbackTransport,
) -> None:
    client = generated_client.GreetingClient(transport)
    transport.publish(
        {
            "jsonrpc": "2.0",
            "method": "greeting.changed",
            "params": {"type": "greeting.said", "text": "Hello!"},
        }
    )

    notification = await anext(client.notifications())

    assert notification.method == "greeting.changed"
    assert notification.params.text == "Hello!"
    assert type(notification.params).__name__ == "GreetingSaid"


async def test_the_client_closes_its_transport(
    generated_client: ModuleType,
    transport: LoopbackTransport,
) -> None:
    async with generated_client.GreetingClient(transport) as client:
        await client.greeting.say(name="Mathis")

    assert transport.closed
