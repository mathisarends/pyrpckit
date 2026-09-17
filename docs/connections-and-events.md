# Connections and events

A connect hook runs before a socket is accepted. Use it to inspect the
handshake, authenticate the peer, and expose a session object to every method
and event on that connection.

```python
from collections.abc import AsyncIterator

from pyrpckit import (
    ConnectionRejected,
    Inject,
    RpcChannel,
    RpcConnection,
    RpcModel,
    RpcRejection,
    RpcService,
)


class Session:
    def __init__(self, user_id: str) -> None:
        self.user_id = user_id


async def authenticate(connection: RpcConnection) -> Session:
    token = connection.headers.get("authorization")
    if token is None:
        raise ConnectionRejected(RpcRejection.UNAUTHORIZED)
    return Session(user_id=token)


tasks = RpcChannel("tasks")
app = RpcService(connect=authenticate)
app.socket("/rpc", tasks)
```

Headers are case-insensitive. `RpcConnection` also exposes `endpoint`, `path`,
`path_params`, `query_params`, `subprotocols`, `client`, and `closed`.

A hook must be async. Its parameters may be `RpcConnection` and `Inject[T]`
dependencies. It may return one concrete type, which then becomes injectable
for the connection lifetime:

```python
@tasks.method()
async def current_user(session: Inject[Session]) -> str:
    return session.user_id
```

Set `connect=` on an individual `app.socket()` or `app.stream()` to override
the service-level hook for that endpoint.

## Reject or close a connection

Raise `ConnectionRejected` inside the connect hook to reject the handshake with
an intentional reason such as `UNAUTHORIZED`, `FORBIDDEN`, or `UNAVAILABLE`.
After acceptance, injected code can close the live connection:

```python
from pyrpckit import RpcConnection, RpcConnectionClose


@tasks.method()
async def sign_out(connection: Inject[RpcConnection]) -> None:
    await connection.close(RpcConnectionClose.NORMAL, reason="Signed out")
```

## Push typed events

An event is an async generator. Each yielded Pydantic value becomes a JSON-RPC
notification sent to every connection on that endpoint:

```python
class TaskUpdated(RpcModel):
    task_id: int
    title: str


class TaskEvents:
    async def subscribe(self) -> AsyncIterator[TaskUpdated]:
        ...


@tasks.event(summary="Publish task changes.")
async def updated(events: Inject[TaskEvents]) -> AsyncIterator[TaskUpdated]:
    async for update in events.subscribe():
        yield update
```

The wire notification name is `tasks.updated`. Events accept only injected
parameters; callers do not subscribe with request parameters. A union of
Pydantic models is supported for event families, and a literal `type` field can
serve as their discriminator in generated clients.

## Runtime limits

Configure per-connection backpressure and message limits with `RpcLimits`:

```python
from pyrpckit import RpcLimits

limits = RpcLimits(
    max_concurrency=16,
    max_queue_size=64,
    max_message_bytes=512_000,
)
```

`max_concurrency` bounds in-flight calls, `max_queue_size` bounds outgoing
responses and events, and `max_message_bytes` rejects oversized incoming
frames. Pass `None` only when intentionally disabling the byte limit.

[Back to documentation](README.md)
