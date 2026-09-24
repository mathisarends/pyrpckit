import subprocess
import sys
from collections.abc import AsyncIterator

import pytest
from fastapi import Depends, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.testclient import TestClient
from pydantic import BaseModel
from starlette.testclient import WebSocketDenialResponse

from rpckit import (
    Inject,
    RpcBinaryInput,
    RpcBinaryOutput,
    RpcChannel,
    RpcConnection,
    RpcDisconnect,
    RpcReject,
    RpcRejection,
    RpcService,
)
from rpckit.fastapi import FastApiSocket, create_router, serve_websocket


class EchoParams(BaseModel):
    value: str


def create_service(
    *, path: str = "/rpc", connections: list[RpcConnection] | None = None
) -> RpcService:
    channel = RpcChannel("demo")

    @channel.server.method()
    async def echo(params: EchoParams, connection: Inject[RpcConnection]) -> EchoParams:
        if connections is not None:
            connections.append(connection)
        return params

    service = RpcService()
    service.socket(path, channels=(channel,))
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

    @channel.server.stream()
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


def test_router_serves_bidirectional_streams_without_cancellation_leaks() -> None:
    channel = RpcChannel("voice")

    @channel.server.stream()
    async def media(
        session_id: int,
        frames: Inject[RpcBinaryInput],
        output: Inject[RpcBinaryOutput],
    ) -> None:
        async for frame in frames:
            await output.send(str(session_id).encode() + b":" + frame)

    service = RpcService()
    service.stream("/voice/{session_id}/media", media)
    web = FastAPI()
    web.include_router(create_router(service))

    with TestClient(web) as client:
        for session_id in range(200):
            with client.websocket_connect(f"/voice/{session_id}/media") as websocket:
                websocket.send_bytes(b"pcm")
                assert websocket.receive_bytes() == f"{session_id}:pcm".encode()
                websocket.send_text('{"type":"end"}')
                assert websocket.receive()["code"] == 1000


def test_router_rejects_invalid_stream_path_variables_as_not_found() -> None:
    channel = RpcChannel("voice")

    @channel.server.stream()
    async def media(session_id: int, output: Inject[RpcBinaryOutput]) -> None: ...

    service = RpcService()
    service.stream("/voice/{session_id}/media", media)
    web = FastAPI()
    web.include_router(create_router(service))

    with (
        TestClient(web) as client,
        pytest.raises(WebSocketDenialResponse) as denied,
        client.websocket_connect("/voice/abc/media"),
    ):
        pass
    assert denied.value.status_code == 404


@pytest.mark.parametrize(
    ("rejection", "status"),
    [(RpcRejection.UNAUTHORIZED, 401), (RpcRejection.FORBIDDEN, 403)],
)
def test_before_accept_rejects_with_http_status_and_headers(rejection, status) -> None:
    service = create_service()

    async def authenticate(handshake):
        if handshake.headers.get("authorization") != "Bearer secret":
            raise RpcReject(
                rejection,
                "Unauthorized",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return None

    web = FastAPI()
    web.include_router(create_router(service, before_accept=authenticate))
    with (
        TestClient(web) as client,
        pytest.raises(WebSocketDenialResponse) as denied,
        client.websocket_connect("/rpc"),
    ):
        pass
    assert denied.value.status_code == status
    assert denied.value.headers["www-authenticate"] == "Bearer"


def test_before_accept_supplies_injected_context() -> None:
    class Principal:
        def __init__(self, name: str) -> None:
            self.name = name

    channel = RpcChannel("auth")

    @channel.server.method()
    async def who(principal: Inject[Principal]) -> str:
        return principal.name

    async def authenticate(handshake):
        return {Principal: Principal("Ada")}

    service = RpcService()
    service.socket("/auth", channels=(channel,), before_accept=authenticate)
    web = FastAPI()
    web.include_router(create_router(service))
    with TestClient(web) as client, client.websocket_connect("/auth") as websocket:
        websocket.send_json({"jsonrpc": "2.0", "id": 1, "method": "auth.who"})
        assert websocket.receive_json()["result"] == "Ada"


def test_serve_websocket_helper_serves_manual_route() -> None:
    service = create_service()
    web = FastAPI()

    @web.websocket("/rpc")
    async def route(websocket: WebSocket) -> None:
        await serve_websocket(service.endpoint("rpc"), websocket)

    with TestClient(web) as client, client.websocket_connect("/rpc") as websocket:
        websocket.send_json(
            {"jsonrpc": "2.0", "id": 1, "method": "demo.echo", "params": {"value": "x"}}
        )
        assert websocket.receive_json()["result"] == {"value": "x"}


def test_import_without_fastapi_reports_missing_extra() -> None:
    code = """
import importlib.abc
import sys

class BlockFastApi(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "fastapi" or fullname.startswith("fastapi."):
            raise ModuleNotFoundError("blocked fastapi")
        return None

sys.meta_path.insert(0, BlockFastApi())
try:
    import rpckit.fastapi
except ModuleNotFoundError as error:
    print(error.name, error, sep="|")
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )

    assert result.stdout.strip() == (
        "fastapi|rpckit.fastapi requires the 'fastapi' extra; install pyrpckit[fastapi]"
    )
