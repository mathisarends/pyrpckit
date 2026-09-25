# Transports

rpckit's core deals in an `RpcSocket` protocol, not a web framework. Use the
FastAPI adapter or implement that small protocol for another server.

## FastAPI

Install the optional adapter:

```bash
uv add "pyrpckit[fastapi]"
```

Mount every endpoint declared on a service:

```python
from fastapi import FastAPI

from rpckit.fastapi import create_router

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

When a resolver can only be built after the app exists, use
`resolver_factory=`. It receives the live `WebSocket` once per connection:

```python
web.include_router(
    create_router(
        app,
        resolver_factory=lambda websocket: Resolver(websocket.app.state.container),
    )
)
```

Configure FastAPI concerns such as the prefix and dependencies through
`include_router()`. The adapter registers JSON-RPC and binary-stream endpoints
as WebSocket routes and maps pre-acceptance rejections to HTTP denial responses
when the server supports the WebSocket denial extension. Without that extension,
the ASGI server falls back to a generic HTTP 403 denial.

FastAPI dependencies on `include_router()` are useful for transport-level
checks such as authentication. Their return values are not injected into RPC
methods; use `context=` or `resolver=` for application dependencies, or mount
endpoints individually with `RpcWebSockets`.

### Mount endpoints with FastAPI dependencies

`RpcWebSockets` mounts single endpoints on an existing router, including
routers from other libraries. Each value in `provide=` is an
`Annotated[T, Depends(...)]`; FastAPI resolves it for the connection, path
parameters included, and handlers receive it as `Inject[T]`:

```python
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from rpckit import RpcRejection
from rpckit.fastapi import RpcWebSockets


async def resolve_job(
    job_id: UUID,
    jobs: Annotated[JobRepository, Depends(get_job_repository)],
) -> Job:
    return await jobs.get_authorized(job_id)


ResolvedJob = Annotated[Job, Depends(resolve_job)]

router = APIRouter(prefix="/jobs")
rpc = RpcWebSockets(
    router,
    provide=[Annotated[Actor, Depends(authenticate)]],
    rejections={
        JobNotFound: RpcRejection.NOT_FOUND,
        JobAccessDenied: RpcRejection.FORBIDDEN,
    },
)
rpc.mount(
    app.endpoint("events"),
    provide=[ResolvedJob, Annotated[JobEvents, Depends(get_job_events)]],
)
rpc.mount(
    app.endpoint("output"),
    provide=[ResolvedJob, Annotated[JobOutput, Depends(get_job_output)]],
)
web.include_router(router)
```

JSON-RPC sockets and binary streams mount the same way. `provide=` on
`RpcWebSockets` applies to every endpoint it mounts. The route path is the
endpoint's declared path, so it always matches the contract; mounting fails
when that path lies outside the router's prefix.

When the context needs more than one dependency per value, decorate a
function instead. It may return one object, keyed by its type, or a mapping
from types to values:

```python
@rpc.context(app.endpoint("events"))
async def job_events(
    job: ResolvedJob,
    hub: Annotated[EventHub, Depends(get_event_hub)],
) -> Mapping[type, object]:
    return {Job: job, JobEvents: hub.events_for(job)}
```

`rejections=` follows
[the core rules](connections-and-events.md#map-failures-to-rejections) and
also covers FastAPI dependencies and the context function: before acceptance
the handshake is rejected with an HTTP denial response, after acceptance the
socket closes with the matching code. Unmapped failures propagate unchanged.
Dependencies passed to `APIRouter(dependencies=...)` run before this check, so
map their failures with FastAPI's own exception handling.

## Custom adapters

Implement `RpcSocket` and pass it to `app.serve(socket)`. The protocol consists
of a handshake property and six async operations:

```python
from rpckit import RpcHandshake


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
parameters, offered subprotocols, and optional client address. Signal client
disconnects by raising `RpcDisconnect` from `receive()` or a send operation.

`app.serve()` matches the handshake path to the declared endpoint. Pass
`root_path=` when an upstream server has already consumed a URL prefix.

For low-level dispatch tests that do not need connection behavior, obtain an
`RpcServer` from `endpoint.create_server(...)` or `channel.create_server(...)` and call
`handle()` or `handle_json()` directly.

[Back to documentation](README.md)
