from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

from backend.features.session.application import (
    SessionImageUrlResolver,
    SessionMessageRuns,
    SessionService,
)
from backend.features.session.presentation.mapper import to_session_response
from backend.features.session.presentation.rpc.coordinator import SessionRunCoordinator
from backend.features.session.presentation.rpc.errors import (
    RunAlreadyActiveRpcError,
    RunNotFoundRpcError,
    SessionAccessDeniedRpcError,
    SessionNotFoundRpcError,
)
from backend.features.session.presentation.rpc.models import (
    MessageSendParams,
    MessageStopParams,
    RunAccepted,
    RunStatus,
    SessionRpcEvent,
    SessionSyncResult,
)
from dishka import AsyncContainer
from fastapi import APIRouter, FastAPI, WebSocket

from pyrpckit import Inject, RpcApp, RpcRouter
from pyrpckit.dishka import DishkaResolver
from pyrpckit.fastapi import RpcWebSocketApp


@dataclass(frozen=True, slots=True)
class SessionConnection:
    session_id: UUID
    user_id: UUID


session_rpc = RpcRouter(
    namespace="session",
    tags=("session",),
    server="session-channel",
)


@session_rpc.notification(
    "event",
    payload=SessionRpcEvent,
    summary="Canonical live session and agent-run update.",
)
async def session_notifications(
    coordinator: Inject[SessionRunCoordinator],
    connection: Inject[SessionConnection],
) -> AsyncIterator[SessionRpcEvent]:
    async with coordinator.subscribe(session_id=connection.session_id) as events:
        async for event in events:
            yield event


@session_rpc.method(
    "sync",
    errors=(SessionNotFoundRpcError, SessionAccessDeniedRpcError),
)
async def sync(
    service: Inject[SessionService],
    image_url_resolver: Inject[SessionImageUrlResolver],
    coordinator: Inject[SessionRunCoordinator],
    connection: Inject[SessionConnection],
) -> SessionSyncResult:
    session = await service.get_by_id(
        session_id=connection.session_id,
        user_id=connection.user_id,
    )
    image_urls = await image_url_resolver.resolve(session=session)
    return SessionSyncResult(
        session=to_session_response(
            session,
            image_urls_by_storage_key=image_urls,
        ),
        active_run=await coordinator.status(session_id=connection.session_id),
    )


@session_rpc.method(
    "message.send",
    errors=(
        SessionNotFoundRpcError,
        SessionAccessDeniedRpcError,
        RunAlreadyActiveRpcError,
    ),
)
async def send_message(
    params: MessageSendParams,
    runs: Inject[SessionMessageRuns],
    connection: Inject[SessionConnection],
) -> RunAccepted:
    return await runs.start(
        session_id=connection.session_id,
        user_id=connection.user_id,
        command=params,
    )


@session_rpc.method(
    "message.stop",
    errors=(
        SessionNotFoundRpcError,
        SessionAccessDeniedRpcError,
        RunNotFoundRpcError,
    ),
)
async def stop_message(
    params: MessageStopParams,
    runs: Inject[SessionMessageRuns],
    connection: Inject[SessionConnection],
) -> RunStatus:
    return await runs.stop(
        session_id=connection.session_id,
        user_id=connection.user_id,
        run_id=params.run_id,
    )


# Compose all RPC feature routers here. More routers can be included the same way.
session_rpc_app = RpcApp()
session_rpc_app.include_router(session_rpc)


def create_session_rpc_runtime(container: AsyncContainer) -> RpcWebSocketApp:
    return RpcWebSocketApp(
        session_rpc_app,
        resolver=DishkaResolver(container),
    )


type AuthenticateWebSocket = Callable[[WebSocket], Awaitable[UUID]]


def register_session_rpc(
    app: FastAPI,
    *,
    container: AsyncContainer,
    authenticate: AuthenticateWebSocket,
) -> None:
    rpc_runtime = create_session_rpc_runtime(container)
    router = APIRouter(prefix="/sessions")

    @router.websocket("/{session_id}/rpc")
    async def session_rpc_endpoint(
        websocket: WebSocket,
        session_id: UUID,
    ) -> None:
        user_id = await authenticate(websocket)
        await rpc_runtime.serve(
            websocket,
            context=SessionConnection(
                session_id=session_id,
                user_id=user_id,
            ),
        )

    app.include_router(router)
