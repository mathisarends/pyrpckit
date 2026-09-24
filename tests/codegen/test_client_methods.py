import asyncio
import importlib
import sys
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, suppress
from copy import deepcopy
from types import ModuleType
from typing import Any

import pytest

from rpckit import (
    Inject,
    RpcChannel,
    RpcClientMethodFailedError,
    RpcConnectedClient,
    RpcDisconnect,
    RpcService,
)
from rpckit.codegen import generate_python_client
from rpckit.codegen.ir import build_ir
from rpckit.codegen.python import PythonClientOptions, render_files
from tests.conftest import (
    MediaPlayParams,
    MediaUnavailableError,
    media_play,
    room_channel,
    room_ping,
)

from ..testing import InMemorySocket

PACKAGE = "client_method_client"

control_channel = RpcChannel("control")


@control_channel.server.method("play")
async def play(client: Inject[RpcConnectedClient]) -> str:
    try:
        result = await client.call(media_play, MediaPlayParams(media_uri="spotify:1"))
    except MediaUnavailableError as error:
        return f"unavailable:{error.details.speaker_id}:{error.message}"
    except RpcClientMethodFailedError as error:
        return f"remote:{error.rpc_code}"
    return f"started:{result.started}"


@control_channel.server.method("ping")
async def ping(client: Inject[RpcConnectedClient]) -> str:
    try:
        await client.call(room_ping)
    except RpcClientMethodFailedError as error:
        return f"remote:{error.rpc_code}"
    return "pong"


@control_channel.server.method("status")
async def status() -> str:
    return "ready"


service = RpcService()
service.socket("/rooms", channels=(room_channel, control_channel), name="rooms")


class SocketAdapter:
    """The generated client's WebSocket protocol over an in-memory socket."""

    def __init__(self, socket: InMemorySocket) -> None:
        self._socket = socket

    async def send(self, message: str) -> None:
        await self._socket.client_send(message)

    async def recv(self) -> str | bytes:
        return await self._socket.client_receive()

    async def close(self) -> None:
        with suppress(RpcDisconnect):
            await self._socket.client_disconnect()


@pytest.fixture(scope="module")
def client_module(tmp_path_factory: pytest.TempPathFactory) -> Iterator[ModuleType]:
    document = service.contract(
        title="Rooms", base_url="wss://rooms.example.com"
    ).to_openrpc()
    root = tmp_path_factory.mktemp("client_methods")
    generate_python_client(
        document,
        root / PACKAGE,
        PythonClientOptions(
            package=PACKAGE,
            client_name="RoomsClient",
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


@asynccontextmanager
async def connected(module: ModuleType, **options: Any) -> AsyncIterator[Any]:
    socket = InMemorySocket("/rooms")
    server = asyncio.create_task(service.serve(socket))

    async def socket_factory(url: str, **_: Any) -> SocketAdapter:
        return SocketAdapter(socket)

    try:
        async with module.RoomsClient.connect(
            socket_factory=socket_factory, **options
        ) as client:
            yield client
    finally:
        with suppress(asyncio.CancelledError):
            await server


def media_handler(module: ModuleType, answer: Any) -> Any:
    class Media(module.RoomMediaHandler):
        async def play(self, params: Any) -> Any:
            return await answer(params)

    return Media()


async def test_a_registered_handler_answers_the_server(
    client_module: ModuleType,
) -> None:
    received = []

    async def answer(params: Any) -> Any:
        received.append(params)
        return client_module.MediaPlayResult(started=True)

    async with connected(
        client_module, handlers=media_handler(client_module, answer)
    ) as client:
        assert await client.control.play() == "started:True"

    assert received[0].media_uri == "spotify:1"
    assert type(received[0]).__name__ == "MediaPlayParams"


async def test_a_declared_error_reaches_the_server_as_its_typed_exception(
    client_module: ModuleType,
) -> None:
    async def answer(params: Any) -> Any:
        raise client_module.MediaUnavailableError.create(
            client_module.SpeakerDetails(speaker_id="s1"),
            message="Speaker offline",
        )

    async with connected(
        client_module, handlers=media_handler(client_module, answer)
    ) as client:
        assert await client.control.play() == "unavailable:s1:Speaker offline"


async def test_a_client_method_without_a_handler_is_answered_with_method_not_found(
    client_module: ModuleType,
) -> None:
    async def answer(params: Any) -> Any:
        return client_module.MediaPlayResult(started=True)

    async with connected(
        client_module, handlers=[media_handler(client_module, answer)]
    ) as client:
        assert await client.control.ping() == "remote:-32601"


async def test_a_client_without_client_methods_answers_method_not_found(
    client_module: ModuleType,
) -> None:
    async with connected(client_module) as client:
        assert await client.control.play() == "remote:-32601"


async def test_a_failing_handler_is_answered_with_an_internal_error(
    client_module: ModuleType,
) -> None:
    async def answer(params: Any) -> Any:
        raise RuntimeError("speaker exploded")

    async with connected(
        client_module, handlers=media_handler(client_module, answer)
    ) as client:
        assert await client.control.play() == "remote:-32603"


async def test_a_slow_handler_does_not_block_the_clients_own_requests(
    client_module: ModuleType,
) -> None:
    release = asyncio.Event()

    async def answer(params: Any) -> Any:
        await release.wait()
        return client_module.MediaPlayResult(started=False)

    async with connected(
        client_module, handlers=media_handler(client_module, answer)
    ) as client:
        playing = asyncio.create_task(client.control.play())
        assert await client.control.status() == "ready"
        release.set()
        assert await playing == "started:False"


def test_one_handler_may_implement_several_namespaces(
    client_module: ModuleType,
) -> None:
    class Room(client_module.RoomHandler, client_module.RoomMediaHandler):
        async def ping(self) -> None: ...

        async def play(self, params: Any) -> Any: ...

    client_module.handler_dispatcher(Room())


def test_handlers_must_implement_a_namespace_once(client_module: ModuleType) -> None:
    class Room(client_module.RoomHandler):
        async def ping(self) -> None: ...

    with pytest.raises(TypeError, match="implements no handler namespace"):
        client_module.handler_dispatcher(object())
    with pytest.raises(ValueError, match="More than one handler"):
        client_module.handler_dispatcher([Room(), Room()])


def test_root_client_methods_generate_a_distinct_root_handler(
    room_document: dict[str, Any],
) -> None:
    document = deepcopy(room_document)
    document["x-rpc-client-methods"][0]["name"] = "ping"

    files = render_files(
        build_ir(document),
        PythonClientOptions(package="root_client"),
    )
    handlers = files["handlers.py"]

    assert "client_methods.py" not in files
    assert "class RootHandler(ABC):" in handlers
    assert "type Handler = RootHandler" in handlers
