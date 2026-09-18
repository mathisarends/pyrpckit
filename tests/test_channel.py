from collections.abc import AsyncIterator

import pytest

from pyrpckit import ProtocolDefinitionError, RpcChannel, RpcError, RpcModel


class SharedError(RpcError):
    pass


class LocalError(RpcError):
    pass


class Event(RpcModel):
    value: str


def test_channel_defaults_and_bare_decorators() -> None:
    channel = RpcChannel("control", raises=[SharedError])

    @channel.method
    async def ping() -> None: ...

    @channel.method(raises=[LocalError])
    async def other() -> None: ...

    assert channel.namespace == "control"
    assert channel.routes[0].name == "control.ping"
    assert channel.routes[1].raises == (SharedError, LocalError)


def test_event_and_stream_inference() -> None:
    channel = RpcChannel("events")

    @channel.event
    async def changed() -> AsyncIterator[Event]:
        yield Event(value="x")

    @channel.stream(content_type="image/png")
    async def frames() -> AsyncIterator[bytes]:
        """Published frames."""
        yield b"x"

    assert channel.events[0].payload is Event
    assert channel.streams[0].name == "events.frames"
    assert channel.streams[0].summary == "Published frames."


def test_freeze_prevents_registration() -> None:
    channel = RpcChannel("control", namespace="")
    channel.freeze()
    with pytest.raises(ProtocolDefinitionError, match="frozen"):
        channel.method()


def test_invalid_stream_type_is_rejected() -> None:
    channel = RpcChannel("files")
    with pytest.raises(ProtocolDefinitionError, match="must yield bytes"):

        @channel.stream()
        async def text() -> AsyncIterator[str]:
            yield "x"


def test_child_channels_inherit_namespace_errors_and_scope() -> None:
    root = RpcChannel("voice", raises=(SharedError,))
    turn = root.child("turn", raises=(LocalError,))

    @root.event()
    async def event() -> AsyncIterator[Event]:
        yield Event(value="root")

    @turn.method()
    async def start() -> None: ...

    protocol = root.freeze()

    assert turn.name == "voice.turn"
    assert turn.namespace == "voice.turn"
    assert turn.raises == (SharedError, LocalError)
    assert protocol.methods[0].name == "voice.turn.start"
    assert protocol.methods[0].raises == (SharedError, LocalError)
    assert protocol.notifications[0].name == "voice.event"


def test_channel_name_defaults_to_namespace() -> None:
    channel = RpcChannel(namespace="voice.turn")

    assert channel.name == "voice.turn"
    assert channel.namespace == "voice.turn"


def test_dotted_operation_name_suggests_a_child_channel() -> None:
    channel = RpcChannel("voice")

    with pytest.raises(ProtocolDefinitionError, match=r"contains '\.'.*child"):
        channel.method("turn.start")
