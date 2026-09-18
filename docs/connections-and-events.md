# Connections and events

`RpcConnection` is available as an injected dependency in methods and events.
Use it to inspect connection metadata or close an accepted connection. Perform
authentication in the hosting framework before calling pyrpckit.

```python
from collections.abc import AsyncIterator

from pyrpckit import (
    Inject,
    RpcChannel,
    RpcConnection,
    RpcModel,
    RpcService,
)

tasks = RpcChannel("tasks")
app = RpcService()
app.socket("/rpc", tasks)
```

Headers are case-insensitive. `RpcConnection` also exposes `endpoint`, `path`,
`path_params`, `query_params`, `subprotocols`, `client`, and `closed`.

Inject it like any other server-side dependency:

```python
@tasks.method()
async def connection_path(connection: Inject[RpcConnection]) -> str:
    return connection.path
```

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
    async def subscribe(self) -> AsyncIterator[TaskUpdated]: ...


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
