import asyncio
import subprocess
import sys
from collections.abc import AsyncIterator, Mapping
from typing import Annotated
from uuid import UUID

import pytest
from fastapi import APIRouter, Depends, FastAPI, WebSocket, WebSocketDisconnect
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
    RpcRejections,
    RpcService,
)
from rpckit.fastapi import (
    FastApiSocket,
    RpcWebSockets,
    create_router,
    serve_websocket,
)


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


class Job:
    def __init__(self, job_id: UUID) -> None:
        self.id = job_id


class JobNotFound(Exception):
    pass


class JobOutput:
    def __init__(self, frames: list[bytes]) -> None:
        self.frames = frames


class Actor:
    def __init__(self, name: str) -> None:
        self.name = name


KNOWN_JOB = UUID(int=1)


async def resolve_job(job_id: UUID) -> Job:
    if job_id != KNOWN_JOB:
        raise JobNotFound("Job not found")
    return Job(job_id)


ResolvedJob = Annotated[Job, Depends(resolve_job)]


def create_job_service(released: list[bool] | None = None) -> RpcService:
    events = RpcChannel("jobs")

    @events.server.method()
    async def describe(job: Inject[Job], actor: Inject[Actor]) -> str:
        return f"{actor.name}:{job.id}"

    output = RpcChannel("output")

    @output.server.stream()
    async def frames(
        job: Inject[Job], source: Inject[JobOutput]
    ) -> AsyncIterator[bytes]:
        try:
            for frame in source.frames:
                yield frame
            await asyncio.Event().wait()
        finally:
            if released is not None:
                released.append(True)

    failing = RpcChannel("failing")

    @failing.server.stream()
    async def broken(job: Inject[Job]) -> AsyncIterator[bytes]:
        yield b"first"
        raise JobNotFound("Job was deleted")

    service = RpcService()
    service.socket("/jobs/{job_id}/events", channels=(events,), name="events")
    service.stream("/jobs/{job_id}/output", frames, name="output")
    service.stream("/jobs/{job_id}/broken", broken, name="broken")
    return service


def create_job_app(
    service: RpcService,
    *,
    rejections: RpcRejections | None = None,
    output_frames: tuple[bytes, ...] = (b"one", b"two"),
) -> FastAPI:
    async def get_output() -> JobOutput:
        return JobOutput(list(output_frames))

    async def get_actor() -> Actor:
        return Actor("ada")

    router = APIRouter(prefix="/jobs")
    rpc = RpcWebSockets(
        router,
        provide=[Annotated[Actor, Depends(get_actor)]],
        rejections=rejections,
    )
    rpc.mount(service.endpoint("events"), provide=[ResolvedJob])
    rpc.mount(
        service.endpoint("output"),
        provide=[ResolvedJob, Annotated[JobOutput, Depends(get_output)]],
    )
    rpc.mount(service.endpoint("broken"), provide=[ResolvedJob])
    web = FastAPI()
    web.include_router(router)
    return web


def test_rpc_websockets_supply_fastapi_dependencies_to_json_rpc_handlers() -> None:
    web = create_job_app(create_job_service())

    with (
        TestClient(web) as client,
        client.websocket_connect(f"/jobs/{KNOWN_JOB}/events") as websocket,
    ):
        websocket.send_json({"jsonrpc": "2.0", "id": 1, "method": "jobs.describe"})
        assert websocket.receive_json()["result"] == f"ada:{KNOWN_JOB}"


def test_rpc_websockets_serve_binary_streams_and_release_them_on_disconnect() -> None:
    released: list[bool] = []
    web = create_job_app(create_job_service(released))

    with (
        TestClient(web) as client,
        client.websocket_connect(f"/jobs/{KNOWN_JOB}/output") as websocket,
    ):
        assert websocket.receive_bytes() == b"one"
        assert websocket.receive_bytes() == b"two"
    assert released == [True]


@pytest.mark.parametrize(
    "rejections",
    [
        {JobNotFound: RpcRejection.NOT_FOUND},
        lambda error: RpcReject(RpcRejection.NOT_FOUND, "Job not found"),
    ],
)
def test_rpc_websockets_reject_dependency_failures_before_accepting(
    rejections,
) -> None:
    web = create_job_app(create_job_service(), rejections=rejections)

    with (
        TestClient(web) as client,
        pytest.raises(WebSocketDenialResponse) as denied,
        client.websocket_connect(f"/jobs/{UUID(int=2)}/events"),
    ):
        pass
    assert denied.value.status_code == 404
    assert denied.value.text == "Job not found"


def test_rpc_websockets_reject_raised_rejections_with_their_headers() -> None:
    async def authenticate() -> Actor:
        raise RpcReject(
            RpcRejection.UNAUTHORIZED,
            "Sign in",
            headers={"WWW-Authenticate": "Bearer"},
        )

    router = APIRouter()
    RpcWebSockets(router).mount(
        create_service().endpoint("rpc"),
        provide=[Annotated[Actor, Depends(authenticate)]],
    )
    web = FastAPI()
    web.include_router(router)

    with (
        TestClient(web) as client,
        pytest.raises(WebSocketDenialResponse) as denied,
        client.websocket_connect("/rpc"),
    ):
        pass
    assert denied.value.status_code == 401
    assert denied.value.headers["www-authenticate"] == "Bearer"


def test_rpc_websockets_preserve_unmapped_dependency_failures() -> None:
    web = create_job_app(create_job_service())

    with (
        TestClient(web) as client,
        pytest.raises(JobNotFound),
        client.websocket_connect(f"/jobs/{UUID(int=2)}/events"),
    ):
        pass


def test_rpc_websockets_close_accepted_streams_with_mapped_rejections() -> None:
    web = create_job_app(
        create_job_service(), rejections={JobNotFound: RpcRejection.NOT_FOUND}
    )

    with (
        TestClient(web) as client,
        client.websocket_connect(f"/jobs/{KNOWN_JOB}/broken") as websocket,
    ):
        assert websocket.receive_bytes() == b"first"
        assert websocket.receive() == {
            "type": "websocket.close",
            "code": 1008,
            "reason": "Job was deleted",
        }


def test_rpc_websockets_context_function_returns_typed_values() -> None:
    service = create_job_service()
    router = APIRouter(prefix="/jobs")
    rpc = RpcWebSockets(router)

    @rpc.context(service.endpoint("events"))
    async def events_context(job: ResolvedJob) -> Mapping[type, object]:
        return {Job: job, Actor: Actor("grace")}

    @rpc.context(service.endpoint("broken"))
    async def broken_context(job: ResolvedJob) -> Job:
        return job

    web = FastAPI()
    web.include_router(router)

    assert events_context.__name__ == "events_context"
    with TestClient(web) as client:
        with client.websocket_connect(f"/jobs/{KNOWN_JOB}/events") as websocket:
            websocket.send_json({"jsonrpc": "2.0", "id": 1, "method": "jobs.describe"})
            assert websocket.receive_json()["result"] == f"grace:{KNOWN_JOB}"
        with client.websocket_connect(f"/jobs/{KNOWN_JOB}/broken") as websocket:
            assert websocket.receive_bytes() == b"first"
        with (
            pytest.raises(JobNotFound),
            client.websocket_connect(f"/jobs/{UUID(int=3)}/broken"),
        ):
            pass


def test_rpc_websockets_use_the_resolver_factory_per_connection() -> None:
    resolved: list[WebSocket] = []

    def resolver_factory(websocket: WebSocket):
        async def resolve(dependency: type) -> Actor:
            resolved.append(websocket)
            return Actor("resolver")

        return resolve

    router = APIRouter(prefix="/jobs")
    rpc = RpcWebSockets(router, resolver_factory=resolver_factory)
    rpc.mount(create_job_service().endpoint("events"), provide=[ResolvedJob])
    web = FastAPI()
    web.include_router(router)

    with (
        TestClient(web) as client,
        client.websocket_connect(f"/jobs/{KNOWN_JOB}/events") as websocket,
    ):
        websocket.send_json({"jsonrpc": "2.0", "id": 1, "method": "jobs.describe"})
        assert websocket.receive_json()["result"] == f"resolver:{KNOWN_JOB}"
    assert len(resolved) == 1


def test_rpc_websockets_reject_invalid_mounts() -> None:
    service = create_job_service()
    rpc = RpcWebSockets(APIRouter(prefix="/job"))

    with pytest.raises(ValueError, match="outside the router prefix"):
        rpc.mount(service.endpoint("events"))
    with pytest.raises(TypeError, match=r"Annotated\[T, Depends"):
        RpcWebSockets(APIRouter(), provide=[Job])
    with pytest.raises(TypeError, match="Job is provided more than once"):
        RpcWebSockets(APIRouter(), provide=[ResolvedJob, ResolvedJob])

    rpc = RpcWebSockets(APIRouter(prefix="/jobs"))
    rpc.mount(service.endpoint("events"))
    with pytest.raises(ValueError, match="already mounted"):
        rpc.mount(service.endpoint("events"))
    with pytest.raises(ValueError, match="mutually exclusive"):
        RpcWebSockets(APIRouter(), resolver=object(), resolver_factory=object())
