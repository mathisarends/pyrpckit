# Callbacks

Methods let the client call the server, and events let the server push
notifications without an answer. Callbacks cover the third direction: the
server sends a request to a connected client and waits for its result or
error.

```python
room_channel.method(...)    # client calls server, server responds
room_channel.event(...)     # server sends, no response
room_channel.callback(...)  # server calls client, client responds
```

JSON-RPC 2.0 assigns the client and server roles per message rather than per
connection, so both sides can send requests on the same socket. The Language
Server Protocol relies on this pattern (`workspace/applyEdit`), and OpenAPI
calls it `callbacks`.

A typical use is a client that dials out to the server and owns local
hardware, such as a room process that controls a speaker. The client stays
unreachable from outside, and the socket gives the server presence for free.
The server can still send it commands and learn the outcome.

## Declare a callback

A callback is a typed declaration, not a decorated function, because the
server has nothing to implement:

```python
from pyrpckit import RpcChannel, RpcError, RpcModel


class MediaPlayParams(RpcModel):
    uri: str


class MediaPlayResult(RpcModel):
    started: bool


class MediaUnavailableError(RpcError):
    rpc_code = -32010


room = RpcChannel("room")
media = room.child("media")

media_play = media.callback(
    "play",
    params=MediaPlayParams,
    result=MediaPlayResult,
    raises=(MediaUnavailableError,),
    summary="Play a media URI on the room's speaker.",
)
```

`callback()` returns an `RpcCallback[MediaPlayParams, MediaPlayResult]` named
`room.media.play`. Like methods, callback names are single segments inside the
channel namespace. Use `channel.child(...)` for nested namespaces. Omit
`params=` for a callback without params, and omit `result=` for one that
answers `null`. A callback name must not collide with a method, event, or
stream name. Channel-level `raises=` apply to methods only, because callback
errors come from the client.

## Call the client

`RpcPeer` represents the connected client. It is injectable wherever
`RpcConnection` is. Keep peers in your own registry to call a client from
anywhere, not only while handling one of its requests:

```python
from pyrpckit import Inject, RpcPeer


class RoomConnections:
    def __init__(self) -> None:
        self.peers: dict[str, RpcPeer] = {}


class HelloParams(RpcModel):
    room_id: str


@room.method("hello")
async def hello(
    params: HelloParams,
    peer: Inject[RpcPeer],
    rooms: Inject[RoomConnections],
) -> None:
    rooms.peers[params.room_id] = peer


async def play(rooms: RoomConnections, room_id: str, uri: str) -> bool:
    peer = rooms.peers[room_id]
    result = await peer.call(media_play, MediaPlayParams(uri=uri), timeout=5.0)
    return result.started
```

`peer.call()` validates the params against the declared model and the answer
against the declared result model. Server-originated requests use string IDs
(`"server:1"`, `"server:2"`, ...), and the reader routes responses to the
peer without ever answering them. `peer.call()` fails as follows:

| Situation | Raised |
| --- | --- |
| The client answers with a declared error code | That `RpcError` subclass, with its details |
| The client answers with any other error | `RpcCallbackRemoteError` (`rpc_code`, `code`, `message`, `details`) |
| The result does not match the declared model | `RpcCallbackResultError` |
| `timeout` elapses; a late response is dropped | `RpcCallbackTimeoutError`, also a `TimeoutError` |
| The connection closes before the answer | `RpcPeerClosedError` |

All of these except the declared errors derive from `RpcCallbackError`. A peer
is bound to its endpoint: calling a callback that the endpoint does not mount
raises `ValueError`. `peer.closed` reports whether the connection has ended,
and `peer.connection` returns its `RpcConnection`. Remove peers from your
registry in a connection-scoped finalizer or when a call raises
`RpcPeerClosedError`.

Outgoing calls respect the endpoint's `RpcLimits`: `max_concurrency` bounds
unanswered callbacks per connection, and a request larger than
`max_message_bytes` raises `ValueError` before it is sent.

## Contract

Callbacks appear under the `x-rpc-callbacks` extension of the OpenRPC document,
next to `x-rpc-notifications`. Entries have the same shape as regular
`methods`: params, result, errors, `x-rpc-params-schema`, and a request schema
named after the callback, such as `RoomMediaPlayCallback`.

## Answer callbacks in a generated Python client

For every callback namespace, the generated client contains an abstract class
named after the namespace path, such as `RoomMediaCallbacks`. Implement the
classes and pass the handlers to `connect()`:

```python
from rooms_client import (
    MediaPlayParams,
    MediaPlayResult,
    MediaUnavailableError,
    RoomMediaCallbacks,
    RoomsClient,
    SpeakerDetails,
)


class Media(RoomMediaCallbacks):
    async def play(self, params: MediaPlayParams) -> MediaPlayResult:
        if not speaker.online:
            raise MediaUnavailableError.create(SpeakerDetails(speaker_id="s1"))
        await speaker.play(params.uri)
        return MediaPlayResult(started=True)


async with RoomsClient.connect(url=url, callbacks=Media()) as client:
    ...
```

`callbacks=` accepts one handler or several. One object may implement several
namespace classes. Registration works per namespace: a callback whose class has
no handler is answered with `-32601 Method not found`, and so is every callback
sent to a client generated without callbacks. Invalid params are answered with
`-32602`. Raise a declared error with its generated `create()` to answer with
that error. Any other exception is logged and answered with `-32603`. Handlers
run concurrently, so a slow handler never delays responses to the client's own
requests.

With custom transports, build the dispatcher yourself and pass it to the
generated transport: `WebSocketTransport(socket, callbacks=callback_dispatcher(Media()))`.
Generated TypeScript clients do not answer callbacks yet.

## Test both directions

`RpcTestClient` registers handlers with `callbacks=`, keyed by the
`RpcCallback` or its wire name. Handlers receive the validated params model, may
be sync or async, and can raise declared `RpcError`s:

```python
from pyrpckit.testing import RpcTestClient


async def test_play_reaches_the_room() -> None:
    async def play(params: MediaPlayParams) -> MediaPlayResult:
        return MediaPlayResult(started=True)

    async with RpcTestClient(app, "/rpc", callbacks={media_play: play}) as client:
        await client.request("room.hello", {"roomId": "kitchen"})
        ...
```

The test client reads the socket in the background, so the server can call it
at any time. Unregistered callbacks are answered with `-32601`.

[Back to documentation](README.md)
