import pytest

from pyrpckit import Inject, RpcCallbackRemoteError, RpcChannel, RpcPeer, RpcService
from pyrpckit.testing import RpcTestClient, RpcTestError

from .conftest import (
    MEDIA_PLAY,
    ROOM_CHANNEL,
    ROOM_PING,
    MediaPlayParams,
    MediaPlayResult,
    MediaUnavailableError,
    SpeakerDetails,
)

CONTROL = RpcChannel("control")


@CONTROL.method("play")
async def play(peer: Inject[RpcPeer]) -> bool:
    result = await peer.call(MEDIA_PLAY, MediaPlayParams(media_uri="spotify:1"))
    return result.started


@CONTROL.method("ping")
async def ping(peer: Inject[RpcPeer]) -> None:
    await peer.call(ROOM_PING)


@CONTROL.method("probe")
async def probe(peer: Inject[RpcPeer]) -> int:
    try:
        await peer.call(ROOM_PING)
    except RpcCallbackRemoteError as error:
        return error.rpc_code
    return 0


SERVICE = RpcService()
SERVICE.socket("/rooms", channels=(ROOM_CHANNEL, CONTROL), name="rooms")


async def test_registered_handlers_answer_callbacks_with_typed_params() -> None:
    received: list[MediaPlayParams] = []

    async def media_play(params: MediaPlayParams) -> MediaPlayResult:
        received.append(params)
        return MediaPlayResult(started=True)

    async with RpcTestClient(
        SERVICE, "/rooms", callbacks={MEDIA_PLAY: media_play}
    ) as client:
        assert await client.request("control.play") is True

    assert received[0].media_uri == "spotify:1"


async def test_handlers_may_be_sync_and_keyed_by_name() -> None:
    async with RpcTestClient(
        SERVICE, "/rooms", callbacks={"room.ping": lambda: None}
    ) as client:
        assert await client.request("control.ping") is None


async def test_declared_errors_raised_by_handlers_reach_the_server() -> None:
    def media_play(params: MediaPlayParams) -> MediaPlayResult:
        raise MediaUnavailableError(SpeakerDetails(speaker_id="s1"))

    async with RpcTestClient(
        SERVICE, "/rooms", callbacks={MEDIA_PLAY: media_play}
    ) as client:
        with pytest.raises(RpcTestError) as error:
            await client.request("control.play")

    assert error.value.code == "media_unavailable"
    assert error.value.details == {"speaker_id": "s1"}


async def test_unregistered_callbacks_are_answered_with_method_not_found() -> None:
    async with RpcTestClient(SERVICE, "/rooms") as client:
        assert await client.request("control.probe") == -32601


def test_handlers_must_name_callbacks_of_the_endpoint() -> None:
    with pytest.raises(ValueError, match="not declared"):
        RpcTestClient(SERVICE, "/rooms", callbacks={"room.missing": lambda: None})
