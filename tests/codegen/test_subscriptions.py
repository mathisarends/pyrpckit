import asyncio
import importlib
import sys
from collections.abc import AsyncIterator
from contextlib import aclosing, suppress
from pathlib import Path

from pyrpckit import RpcChannel, RpcDisconnect, RpcModel, RpcService
from pyrpckit.codegen import generate_python_client
from pyrpckit.codegen.python import PythonClientOptions
from tests.testing import InMemorySocket


class SessionParams(RpcModel):
    session_id: str


class SessionEvent(RpcModel):
    value: str


async def test_generated_python_subscription_roundtrip(tmp_path: Path) -> None:
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
    package = "subscription_client"
    generate_python_client(
        service.contract(title="Session", base_url="ws://localhost").to_openrpc(),
        tmp_path / package,
        PythonClientOptions(
            package=package,
            client_name="SessionClient",
            with_transport="websocket",
        ),
    )
    sys.path.insert(0, str(tmp_path))
    importlib.invalidate_caches()
    try:
        module = importlib.import_module(package)
        socket = InMemorySocket("/rpc")
        server = asyncio.create_task(service.serve(socket))

        class SocketAdapter:
            async def send(self, message: str) -> None:
                await socket.client_send(message)

            async def recv(self) -> str | bytes:
                return await socket.client_receive()

            async def close(self) -> None:
                with suppress(RpcDisconnect):
                    await socket.client_disconnect()

        async def socket_factory(*_: object, **__: object) -> SocketAdapter:
            return SocketAdapter()

        try:
            async with module.SessionClient.connect(
                socket_factory=socket_factory
            ) as client:
                stream = client.session.events(session_id="abc")
                async with aclosing(stream):
                    result = await asyncio.wait_for(anext(stream), 1)
                    assert result.value == "abc"
                await asyncio.wait_for(stopped.wait(), 1)
        finally:
            await server
    finally:
        sys.path.remove(str(tmp_path))
        for name in list(sys.modules):
            if name == package or name.startswith(f"{package}."):
                del sys.modules[name]
