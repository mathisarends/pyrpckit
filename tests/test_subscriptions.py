import asyncio
import json
from collections.abc import AsyncIterator

from rpckit import RpcChannel, RpcLimits, RpcModel, RpcService

from .testing import InMemorySocket


class SessionParams(RpcModel):
    session_id: str


class SessionEvent(RpcModel):
    value: str


async def test_subscription_params_notifications_and_cleanup() -> None:
    channel = RpcChannel("session")
    stopped = asyncio.Event()

    @channel.server.subscription()
    async def events(params: SessionParams) -> AsyncIterator[SessionEvent]:
        try:
            yield SessionEvent(value=params.session_id)
            await asyncio.Future()
        finally:
            stopped.set()

    service = RpcService()
    service.socket("/rpc", channels=(channel,))
    socket = InMemorySocket("/rpc")
    task = asyncio.create_task(service.serve(socket))
    await asyncio.sleep(0)
    await socket.client_send(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "session.events.subscribe",
                "params": {"sessionId": "abc"},
            }
        )
    )
    response = json.loads(await asyncio.wait_for(socket.client_receive(), 1))
    subscription_id = response["result"]["subscriptionId"]
    notification = json.loads(await asyncio.wait_for(socket.client_receive(), 1))
    assert notification == {
        "jsonrpc": "2.0",
        "method": "session.events",
        "params": {
            "subscriptionId": subscription_id,
            "payload": {"value": "abc"},
        },
    }
    await socket.client_send(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "session.events.unsubscribe",
                "params": {"subscriptionId": subscription_id},
            }
        )
    )
    assert (
        json.loads(await asyncio.wait_for(socket.client_receive(), 1))["result"] is None
    )
    await asyncio.wait_for(stopped.wait(), 1)
    stopped.clear()
    await socket.client_send(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "session.events.subscribe",
                "params": {"sessionId": "second"},
            }
        )
    )
    assert "result" in json.loads(await asyncio.wait_for(socket.client_receive(), 1))
    await asyncio.wait_for(socket.client_receive(), 1)
    await socket.client_disconnect()
    await task
    assert stopped.is_set()


async def test_subscription_limit_and_contract() -> None:
    channel = RpcChannel("session")

    @channel.server.subscription()
    async def events(params: SessionParams) -> AsyncIterator[SessionEvent]:
        await asyncio.Future()
        yield SessionEvent(value=params.session_id)

    service = RpcService()
    service.socket("/rpc", channels=(channel,))
    document = service.contract(title="Session", base_url="ws://localhost").to_openrpc()
    assert document["x-rpc-subscriptions"][0]["name"] == "session.events"
    socket = InMemorySocket("/rpc")
    task = asyncio.create_task(
        service.serve(socket, limits=RpcLimits(max_subscriptions=1))
    )
    await asyncio.sleep(0)
    for request_id in (1, 2):
        await socket.client_send(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "session.events.subscribe",
                    "params": {"sessionId": "abc"},
                }
            )
        )
        response = json.loads(await asyncio.wait_for(socket.client_receive(), 1))
        assert ("result" in response) is (request_id == 1)
    await socket.client_disconnect()
    await task
