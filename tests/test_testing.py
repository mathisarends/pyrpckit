import pytest

from pyrpckit import Inject, RpcChannel, RpcClientMethodFailedError, RpcPeer, RpcService
from pyrpckit.testing import RpcTestClient, RpcTestError

from .conftest import (
    MediaPlayParams,
    MediaPlayResult,
    MediaUnavailableError,
    SpeakerDetails,
    media_play,
    room_channel,
    room_ping,
)

control_channel = RpcChannel("control")


@control_channel.server.method("play")
async def play(peer: Inject[RpcPeer]) -> bool:
    result = await peer.call(media_play, MediaPlayParams(media_uri="spotify:1"))
    return result.started


@control_channel.server.method("ping")
async def ping(peer: Inject[RpcPeer]) -> None:
    await peer.call(room_ping)


@control_channel.server.method("probe")
async def probe(peer: Inject[RpcPeer]) -> int:
    try:
        await peer.call(room_ping)
    except RpcClientMethodFailedError as error:
        return error.rpc_code
    return 0


service = RpcService()
service.socket("/rooms", channels=(room_channel, control_channel), name="rooms")


async def test_registered_handlers_answer_client_methods_with_typed_params() -> None:
    received: list[MediaPlayParams] = []

    async def play(params: MediaPlayParams) -> MediaPlayResult:
        received.append(params)
        return MediaPlayResult(started=True)

    async with RpcTestClient(
        service, "/rooms", client_methods={media_play: play}
    ) as client:
        assert await client.request("control.play") is True

    assert received[0].media_uri == "spotify:1"


async def test_handlers_may_be_sync_and_keyed_by_name() -> None:
    async with RpcTestClient(
        service, "/rooms", client_methods={"room.ping": lambda: None}
    ) as client:
        assert await client.request("control.ping") is None


async def test_declared_errors_raised_by_handlers_reach_the_server() -> None:
    def play(params: MediaPlayParams) -> MediaPlayResult:
        raise MediaUnavailableError(SpeakerDetails(speaker_id="s1"))

    async with RpcTestClient(
        service, "/rooms", client_methods={media_play: play}
    ) as client:
        with pytest.raises(RpcTestError) as error:
            await client.request("control.play")

    assert error.value.code == "media_unavailable"
    assert error.value.details == {"speaker_id": "s1"}


async def test_unregistered_client_methods_are_answered_with_method_not_found() -> None:
    async with RpcTestClient(service, "/rooms") as client:
        assert await client.request("control.probe") == -32601


def test_handlers_must_name_client_methods_of_the_endpoint() -> None:
    with pytest.raises(ValueError, match="not declared"):
        RpcTestClient(service, "/rooms", client_methods={"room.missing": lambda: None})
