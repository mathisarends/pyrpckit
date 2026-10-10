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
endpoints individually with `RpcRoutes`.

### Mount endpoints with FastAPI dependencies

`RpcRoutes` mounts endpoints on an existing router, including routers from
other libraries. An endpoint declares the type of its connection context with
`context=`; its handlers receive that value as `Inject[T]`:

```python
from collections.abc import AsyncIterator

from rpckit import Inject, RpcChannel, RpcService

output = RpcChannel("output")


@output.stream(content_type="text/plain")
async def lines(job: Inject[Job]) -> AsyncIterator[bytes]:
    async for line in job.output():
        yield line


app = RpcService()
job_events = app.socket("/jobs/{job_id}/events", channels=(jobs,), context=Job)
job_output = app.stream("/jobs/{job_id}/output", lines, context=Job)
```

`RpcRoutes` supplies that value through an async context function. It runs as a
regular FastAPI dependency per connection, path parameters included:

```python
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from rpckit.fastapi import RpcRoutes


async def open_job(
    job_id: UUID,
    actor: Annotated[Actor, Depends(authenticate)],
    jobs: Annotated[JobRepository, Depends(get_job_repository)],
) -> Job:
    return await jobs.get_authorized(job_id, actor)


router = APIRouter(prefix="/jobs")
job_routes = RpcRoutes(
    router,
    context=open_job,
    rejects=[job_not_found, job_access_denied],
)
job_routes.mount(job_events)
job_routes.mount(job_output)
web.include_router(router)
```

The routes take their type from the context function's return annotation, so
`job_routes` is an `RpcRoutes[Job]` and type checkers reject endpoints that
declare another context or none. `mount()` checks the same at runtime; a
context function annotated with a subclass of the declared type is accepted.
Routes without `context=` mount endpoints without a declared context. To supply
several values, declare a dataclass as the context.

`mount()` adds one WebSocket route at the endpoint's declared path, so it
always matches the contract. Mounting fails when that path lies outside the
router's prefix or the endpoint is already mounted. JSON-RPC sockets and binary
streams mount the same way.

`resolver=` accepts any rpckit resolver for the remaining `Inject[T]` values.
To integrate a DI library, pass an object implementing `FastApiResolver`
instead: it creates a resolver per WebSocket and may wrap the context function.
`rpckit.dishka.Dishka` is one such [integration](dependencies.md#dishka).

`rejects=` takes [error bindings](errors.md#bind-domain-exceptions) with a
`rejection=`, and `rejections=` takes a mapping or callable. Both follow
[the core rules](connections-and-events.md#map-failures-to-rejections), are
consulted before the `rejects=` of the mounted endpoint and its service, and
also cover the context function and its dependencies: before acceptance the
handshake is rejected with an HTTP denial response, after acceptance the
socket closes with the matching code. Unmapped failures propagate unchanged.
Dependencies passed to `APIRouter(dependencies=...)` run before this check, so
map their failures with FastAPI's own exception handling.

### Mount a custom connection handler

Pass `handler=` to `mount()` when an endpoint needs an application-owned
connection lifecycle, such as sending a pending status before waiting for a
resource:

```python
from fastapi import WebSocket

from rpckit import Inject
from rpckit.dishka import Dishka
from rpckit.fastapi import RpcRoutes


async def serve_job(
    websocket: WebSocket,
    job: Inject[Job],
    lifecycle: Inject[JobLifecycle],
) -> None:
    await lifecycle.run(websocket, job_id=job.id)


job_routes = RpcRoutes(router, context=open_job, resolver=Dishka())
job_routes.mount(job_events, handler=serve_job)
```

`JobLifecycle` is an application service. The handler uses the same `Inject[T]`
syntax as RPC methods: the endpoint's declared context type receives the
authorized result of `context=`, and other dependencies come from `resolver=`.
Parameter names are arbitrary. Context parameters must use `Inject[T]`; an
unmarked context or an injected subclass of the declared context type fails
during mounting. Handlers must be async functions with positional-or-keyword
or keyword-only parameters. Other parameters retain normal FastAPI dependency,
path, and query resolution.

The context dependency runs even when the handler does not request its result.
After successful dependency resolution, rpckit enters one connection scope,
adds the context under the endpoint's declared type, resolves the handler's
`Inject[T]` parameters, and calls the handler. The scope opens before the
handler accepts the socket and exits on completion, failure, or cancellation.
With Dishka, injected services live in the rpckit SESSION scope and can depend
on the context through `from_context`; REQUEST-scoped services belong in RPC
calls. Neither the context nor `Inject[T]` requires a Dishka `@inject` decorator.
See [Dishka integration](dependencies.md#dishka) for application setup and the
separate FastAPI dependency scope.

The route retains the endpoint's path, name, prefix validation, duplicate-mount
checks, and rejection policy. Declared failures in FastAPI dependencies,
handler injection, the handler, or connection-scope cleanup reject the
handshake before acceptance and close an accepted socket afterwards. An already
closed socket is not closed again; unmapped failures propagate.

The handler owns acceptance (including any subprotocol), messages, resource
acquisition, disconnect handling, cancellation of its tasks, and socket closure.
rpckit does not start a serving loop or create runtime objects such as
`RpcConnection` for a custom handler. Standard runtime settings such as `limits=`
and `error_mapper=` are applied by the standard serving runtime, not by the
custom handler wrapper. Changing the mounting handler does not change the
exported contract; the handler must implement the endpoint's declared protocol.

### Write your own route

When a route needs its own code around the connection, such as a path that
differs from the contract, call `serve_websocket()` from a regular FastAPI
WebSocket route. It serves JSON-RPC sockets and binary streams alike, accepts
the same runtime options as `create_router()`, and returns quietly when
Starlette cancels the task after a disconnect:

```python
from fastapi import WebSocket

from rpckit.fastapi import serve_websocket


@web.websocket("/legacy/rpc")
async def legacy_rpc(websocket: WebSocket) -> None:
    await serve_websocket(app.endpoint("events"), websocket, context={Clock: clock})
```

Prefer `RpcRoutes` for routes that only need FastAPI dependencies.

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
