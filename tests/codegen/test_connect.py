import asyncio
import importlib
import json
import sys
from collections.abc import Iterator
from copy import deepcopy
from types import ModuleType
from typing import Any

import pytest

from pyrpckit.codegen import generate_python_client
from pyrpckit.codegen.python import PythonClientOptions

PACKAGE = "connect_client"


class FakeSocket:
    """Answers every request with the same result, over JSON text frames."""

    def __init__(self, url: str, result: Any) -> None:
        self.url = url
        self.closed = False
        self._result = result
        self._replies: asyncio.Queue[str] = asyncio.Queue()

    async def send(self, message: str) -> None:
        request = json.loads(message)
        self._replies.put_nowait(
            json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": self._result})
        )

    async def recv(self) -> str:
        return await self._replies.get()

    async def close(self) -> None:
        self.closed = True


class FakeNetwork:
    def __init__(self, result: Any = None) -> None:
        self.sockets: list[FakeSocket] = []
        self.headers: list[dict[str, str] | None] = []
        self.result = result

    async def __call__(
        self,
        url: str,
        *,
        subprotocols: list[str] | None,
        additional_headers: dict[str, str] | None,
    ) -> FakeSocket:
        socket = FakeSocket(url, self.result)
        self.sockets.append(socket)
        self.headers.append(additional_headers)
        return socket

    @property
    def urls(self) -> list[str]:
        return [socket.url for socket in self.sockets]


@pytest.fixture(scope="module")
def client_module(
    document: dict[str, Any],
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[ModuleType]:
    deployed = deepcopy(document)
    deployed["methods"] = [deployed["methods"][0]]
    deployed["methods"][0]["servers"] = [{"name": "primary"}]
    deployed["x-rpc-notifications"] = []
    deployed["servers"] = [
        {
            "name": "primary",
            "url": "wss://{host}/rpc",
            "variables": {"host": {"default": "api.example.com"}},
            "x-rpckit-transport": {"type": "websocket", "messageEncoding": "json"},
        },
        {
            "name": "secondary",
            "url": "wss://{host}/second",
            "variables": {"host": {"default": "api.example.com"}},
            "x-rpckit-transport": {"type": "websocket", "messageEncoding": "json"},
        },
    ]
    deployed["x-rpckit-binary-streams"] = [
        {
            "name": "greeting.frames",
            "url": "wss://{host}/frames",
            "direction": "server-to-client",
            "contentType": "image/jpeg",
            "frameType": "binary",
            "variables": {"host": {"default": "api.example.com"}},
        }
    ]
    root = tmp_path_factory.mktemp("connect")
    generate_python_client(
        deployed,
        root / PACKAGE,
        PythonClientOptions(
            package=PACKAGE,
            client_name="GreetingClient",
            with_transport="websocket",
        ),
    )
    sys.path.insert(0, str(root))
    importlib.invalidate_caches()
    try:
        yield importlib.import_module(PACKAGE)
    finally:
        sys.path.remove(str(root))
        for name in [
            name
            for name in sys.modules
            if name == PACKAGE or name.startswith(f"{PACKAGE}.")
        ]:
            del sys.modules[name]


async def test_connect_opens_a_socket_only_for_the_server_that_is_used(
    client_module: ModuleType,
) -> None:
    network = FakeNetwork({"text": "Hello, Mathis!"})

    async with client_module.GreetingClient.connect(
        host="stage.example.com",
        socket_factory=network,
    ) as client:
        assert network.urls == []

        result = await client.greeting.say(name="Mathis")

        assert result.text == "Hello, Mathis!"
        assert network.urls == ["wss://stage.example.com/rpc"]

    assert all(socket.closed for socket in network.sockets)


async def test_connecting_eagerly_opens_every_declared_server(
    client_module: ModuleType,
) -> None:
    network = FakeNetwork()

    async with client_module.GreetingClient.connect(
        host="stage.example.com",
        socket_factory=network,
        eager=True,
    ):
        assert sorted(network.urls) == [
            "wss://stage.example.com/rpc",
            "wss://stage.example.com/second",
        ]


async def test_connect_forwards_headers_to_websocket_factories(
    client_module: ModuleType,
) -> None:
    network = FakeNetwork({"text": "Hello, Mathis!"})

    client = await client_module.GreetingClient.connect(
        socket_factory=network,
        headers={"Authorization": "Bearer secret"},
    )
    try:
        await client.greeting.say(name="Mathis")
    finally:
        await client.close()

    assert network.headers == [{"Authorization": "Bearer secret"}]


async def test_a_single_server_can_be_pointed_somewhere_else(
    client_module: ModuleType,
) -> None:
    network = FakeNetwork({"text": "Hello, Mathis!"})

    async with client_module.GreetingClient.connect(
        host="stage.example.com",
        servers={client_module.ServerName.PRIMARY: "wss://localhost:8000/rpc"},
        socket_factory=network,
    ) as client:
        await client.greeting.say(name="Mathis")

        assert network.urls == ["wss://localhost:8000/rpc"]


def test_an_endpoint_override_must_match_its_server(
    client_module: ModuleType,
) -> None:
    mismatched = client_module.Endpoint(
        server=client_module.ServerName.SECONDARY,
        url="wss://secondary.example.com/rpc",
    )

    with pytest.raises(ValueError, match="Endpoint override.*declares"):
        client_module.GreetingClient.connect(
            servers={client_module.ServerName.PRIMARY: mismatched}
        )


async def test_binary_streams_reuse_the_host_the_client_connected_with(
    client_module: ModuleType,
) -> None:
    network = FakeNetwork()
    opened: list[str] = []

    async def stream_factory(
        url: str,
        *,
        subprotocols: list[str] | None,
        additional_headers: dict[str, str] | None,
    ) -> FakeSocket:
        opened.append(url)
        return FakeSocket(url, None)

    async with client_module.GreetingClient.connect(
        host="stage.example.com",
        socket_factory=network,
        stream_socket_factory=stream_factory,
    ) as client:
        async with client.greeting.frames():
            pass
        async with client.greeting.frames(host="other.example.com"):
            pass

    assert opened == [
        "wss://stage.example.com/frames",
        "wss://other.example.com/frames",
    ]


async def test_an_unknown_stream_variable_is_rejected(
    client_module: ModuleType,
) -> None:
    streams = importlib.import_module(f"{PACKAGE}.streams")
    info = streams.BINARY_STREAMS[streams.BinaryStreamName.GREETING_FRAMES]

    with pytest.raises(ValueError, match="Unknown variables"):
        streams.resolve_stream_endpoint(info, {"token": "secret"})
