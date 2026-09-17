# Transports

pyrpckit's core deals in an `RpcSocket` protocol, not a web framework. Use the
FastAPI adapter, implement that small protocol for another server, or test the
service entirely in memory.

## FastAPI

Install the optional adapter:

```bash
uv add "pyrpckit[fastapi]"
```

Mount every endpoint declared on a service:

```python
from fastapi import FastAPI

from pyrpckit.fastapi import create_router

web = FastAPI()
web.include_router(create_router(app))
```

Common runtime options are configured once on the router:

```python
from fastapi import Depends


web.include_router(
    create_router(
        app,
        resolver=resolver,
        context={Settings: settings},
        limits=limits,
        error_mapper=map_error,
    ),
    prefix="/api",
    dependencies=[Depends(authenticate)],
)
```

Configure FastAPI concerns such as the prefix and dependencies through
`include_router()`. The adapter registers JSON-RPC and binary-stream endpoints
as WebSocket routes and maps pre-acceptance rejections to HTTP denial responses
when the server supports the WebSocket denial extension. Without that extension,
the ASGI server falls back to a generic HTTP 403 denial.

FastAPI dependencies are useful for transport-level checks such as
authentication. Their return values are not injected into RPC methods; use
`context=` or `resolver=` for application dependencies.

## Custom adapters

Implement `RpcSocket` and pass it to `app.serve(socket)`. The protocol consists
of a handshake property and six async operations:

```python
from pyrpckit import RpcHandshake


class MySocket:
    @property
    def handshake(self) -> RpcHandshake: ...

    async def accept(self, subprotocol: str | None = None) -> None: ...
    async def reject(self, rejection, reason: str) -> None: ...
    async def receive(self) -> str | bytes: ...
    async def send(self, message: str) -> None: ...
    async def send_bytes(self, data: bytes) -> None: ...
    async def close(self, close, reason: str) -> None: ...
```

`RpcHandshake` carries the request path, headers, query parameters, path
parameters, offered subprotocols, and optional client address. Signal peer
disconnects by raising `RpcDisconnect` from `receive()` or a send operation.

`app.serve()` matches the handshake path to the declared endpoint. Pass
`root_path=` when an upstream server has already consumed a URL prefix.

## Test without a network server

`RpcTestClient` runs the same service runtime over an in-memory socket:

```python
from pyrpckit.testing import RpcTestClient, RpcTestError


async with RpcTestClient(app, "/rpc", context={TaskStore: store}) as client:
    result = await client.request("tasks.create", {"title": "Test it"})
    await client.notify("tasks.refresh")
    method, payload = await client.next_notification()
```

Expected RPC failures are raised as `RpcTestError`, carrying `rpc_code`, the
stable application `code`, `message`, and `details`. The client also exposes
its in-memory `socket`, which makes acceptance, rejection, subprotocol, and
close behavior directly assertable.

For low-level dispatch tests that do not need connection behavior, obtain an
`RpcServer` from `endpoint.server(...)` or `channel.server(...)` and call
`handle()` or `handle_json()` directly.

[Back to documentation](README.md)
