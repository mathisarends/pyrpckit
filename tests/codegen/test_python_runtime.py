import importlib
import sys
from collections.abc import AsyncIterator
from copy import deepcopy
from types import ModuleType
from typing import Any

import pytest
from pydantic import TypeAdapter

from rpckit.codegen import generate_python_client
from rpckit.codegen.python import PythonClientOptions


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
            "direction": "server-to-client",
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

    async def factory(
        url: str,
        *,
        subprotocols: list[str] | None,
        additional_headers: dict[str, str] | None,
    ):
        assert url == "wss://media/turn-1"
        assert subprotocols is None
        assert additional_headers is None
        return socket

    try:
        media = importlib.import_module(f"{package}.streams")
        endpoint = media.BinaryStreamEndpoint(
            name=media.BinaryStreamName.VOICE,
            url="wss://media/turn-1",
            content_type="audio/pcm;rate=24000",
        )
        stream = await media.BinaryWebSocketStream.open(
            endpoint, socket_factory=factory
        )

        assert await stream.receive() == b"assistant-pcm"
        await stream.close()

        assert socket.sent == []
        assert socket.closed is True
    finally:
        sys.path.remove(str(tmp_path))
        for name in [
            name
            for name in sys.modules
            if name == package or name.startswith(f"{package}.")
        ]:
            del sys.modules[name]


class ClosingTransport:
    """A transport whose notification stream ends right away."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.closed = 0

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        return None

    async def notifications(self) -> AsyncIterator[dict[str, Any]]:
        if self.error is not None:
            raise self.error
        if False:
            yield {}

    async def close(self) -> None:
        self.closed += 1


async def _drain(stream: AsyncIterator[dict[str, Any]]) -> list[dict[str, Any]]:
    return [message async for message in stream]


async def test_subscribing_after_the_pump_ended_stops_instead_of_hanging(
    runtime: ModuleType,
) -> None:
    core = runtime.RpcClientCore(ClosingTransport())

    assert await _drain(core.notifications("tasks.updated")) == []
    assert await _drain(core.notifications("tasks.updated")) == []


async def test_subscribing_after_the_pump_failed_reports_the_failure(
    runtime: ModuleType,
) -> None:
    core = runtime.RpcClientCore(ClosingTransport(RuntimeError("socket died")))

    with pytest.raises(runtime.RpcTransportError):
        await _drain(core.notifications("tasks.updated"))
    with pytest.raises(runtime.RpcTransportError):
        await _drain(core.notifications("tasks.updated"))
