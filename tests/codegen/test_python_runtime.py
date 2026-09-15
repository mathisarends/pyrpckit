import importlib
import sys
from collections.abc import AsyncIterator
from copy import deepcopy
from types import ModuleType
from typing import Any

import pytest
from pydantic import TypeAdapter

from pyrpckit.codegen import generate_python_client
from pyrpckit.codegen.python import PythonClientOptions


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


async def test_binary_websocket_stream_sends_and_receives_raw_frames(
    document: dict[str, Any], tmp_path
) -> None:
    deployed = deepcopy(document)
    deployed["servers"] = [
        {
            "name": "control",
            "url": "wss://api/rpc",
            "x-rpckit-transport": {
                "type": "websocket",
                "messageEncoding": "json",
            },
        }
    ]
    deployed["x-rpckit-binary-streams"] = [
        {
            "name": "voice",
            "url": "wss://media/{sessionId}",
            "direction": "bidirectional",
            "contentType": "audio/pcm;rate=24000",
            "frameType": "binary",
            "variables": {"sessionId": {"default": "demo"}},
        }
    ]
    package = "voice_client"
    generate_python_client(
        deployed,
        tmp_path / package,
        PythonClientOptions(package=package, with_transport="websocket"),
    )
    sys.path.insert(0, str(tmp_path))

    class Socket:
        def __init__(self) -> None:
            self.sent: list[bytes] = []
            self.closed = False

        async def send(self, message: bytes) -> None:
            self.sent.append(message)

        async def recv(self) -> bytes:
            return b"assistant-pcm"

        async def close(self) -> None:
            self.closed = True

    socket = Socket()

    async def factory(url: str, *, subprotocols: list[str] | None):
        assert url == "wss://media/turn-1"
        assert subprotocols is None
        return socket

    try:
        media = importlib.import_module(f"{package}.media")
        endpoint = media.voice(session_id="turn-1")
        stream = await media.BinaryWebSocketStream.open(
            endpoint, socket_factory=factory
        )

        await stream.send(b"microphone-pcm")
        assert await stream.receive() == b"assistant-pcm"
        await stream.close()

        assert socket.sent == [b"microphone-pcm"]
        assert socket.closed is True
    finally:
        sys.path.remove(str(tmp_path))
        for name in [
            name
            for name in sys.modules
            if name == package or name.startswith(f"{package}.")
        ]:
            del sys.modules[name]
