from collections.abc import AsyncIterator

from fastapi import Depends, FastAPI, WebSocketDisconnect
from fastapi.testclient import TestClient
from pydantic import BaseModel

from pyrpckit import Inject, RpcChannel, RpcConnection, RpcDisconnect, RpcService
from pyrpckit.fastapi import FastApiSocket, create_router


class EchoParams(BaseModel):
    value: str


def create_service(
    *, path: str = "/rpc", connections: list[RpcConnection] | None = None
) -> RpcService:
    channel = RpcChannel("demo")

    @channel.method()
    async def echo(params: EchoParams, connection: Inject[RpcConnection]) -> EchoParams:
        if connections is not None:
            connections.append(connection)
        return params

    service = RpcService()
    service.socket(path, channel)
    return service


def test_router_serves_websocket_with_fastapi_options() -> None:
    dependencies_called = []
    handshakes = []

    async def dependency() -> None:
        dependencies_called.append(True)

    web = FastAPI()
    web.include_router(
        create_router(create_service(path="/rpc/{endpoint}", connections=handshakes)),
        prefix="/api",
        dependencies=[Depends(dependency)],
    )

    with (
        TestClient(web) as client,
        client.websocket_connect(
            "/api/rpc/primary?endpoint=ignored&view=compact",
            headers={"x-test": "yes"},
            subprotocols=["rpc.test"],
        ) as websocket,
    ):
        websocket.send_json(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "demo.echo",
                "params": {"value": "hello"},
            }
        )
        assert websocket.receive_json() == {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"value": "hello"},
        }

    assert dependencies_called == [True]
    assert len(handshakes) == 1
    connection = handshakes[0]
    assert connection.path == "/api/rpc/primary"
    assert connection.path_params == {"endpoint": "primary"}
    assert connection.query_params == {"endpoint": "ignored", "view": "compact"}
    assert connection.headers["x-test"] == "yes"
    assert connection.subprotocols == ("rpc.test",)
    assert connection.client is not None


def test_repeated_test_client_disconnects_do_not_leak_cancellation() -> None:
    web = FastAPI()
    web.include_router(create_router(create_service()))

    with TestClient(web) as client:
        for value in range(200):
            with client.websocket_connect("/rpc") as websocket:
                websocket.send_json(
                    {
                        "jsonrpc": "2.0",
                        "id": value,
                        "method": "demo.echo",
                        "params": {"value": str(value)},
                    }
                )
                assert websocket.receive_json()["result"] == {"value": str(value)}


def test_router_serves_binary_stream() -> None:
    channel = RpcChannel("media")

    @channel.stream()
    async def frames() -> AsyncIterator[bytes]:
        yield b"frame"

    service = RpcService()
    service.stream("/frames", frames)
    web = FastAPI()
    web.include_router(create_router(service))

    with (
        TestClient(web) as client,
        client.websocket_connect("/frames") as websocket,
    ):
        assert websocket.receive_bytes() == b"frame"


async def test_send_translates_websocket_disconnect() -> None:
    class DisconnectedWebSocket:
        async def send_text(self, message: str) -> None:
            raise WebSocketDisconnect()

    socket = FastApiSocket.__new__(FastApiSocket)
    socket._websocket = DisconnectedWebSocket()

    try:
        await socket.send("message")
    except RpcDisconnect:
        pass
    else:
        raise AssertionError("RpcDisconnect was not raised")


async def test_send_preserves_unexpected_runtime_errors() -> None:
    class BrokenWebSocket:
        async def send_text(self, message: str) -> None:
            raise RuntimeError("invalid WebSocket state")

    socket = FastApiSocket.__new__(FastApiSocket)
    socket._websocket = BrokenWebSocket()

    try:
        await socket.send("message")
    except RuntimeError as error:
        assert str(error) == "invalid WebSocket state"
    else:
        raise AssertionError("RuntimeError was not raised")
