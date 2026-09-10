# Improve URL ergonomics

## Goal

Methods and events that share a WebSocket should be colocated. The user should
not pass a large endpoint object to every decorator, and a router-wide server
setting should not silently assign unrelated methods to the same socket.

The API can break completely while the project is experimental. Do not retain
aliases or compatibility behavior for `server=`, `OpenRpcServer`,
`include_router()`, or `notification()` if the new model replaces them.

## Proposed API

Use a FastAPI-style router that creates explicit WebSocket channels. A channel
owns its connection setup, RPC methods, and outgoing events.

```python
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import Depends, FastAPI, WebSocket

from pyrpckit import Inject
from pyrpckit.dishka import DishkaResolver
from pyrpckit.fastapi import RpcAPIRouter


browser_rpc = RpcAPIRouter(
    prefix="/sessions/{session_id}",
    resolver=DishkaResolver(container),
)

control = browser_rpc.websocket(
    "/control",
    name="browser-control",
    namespace="browser.control",
    tags=("browser", "control"),
)

screencast = browser_rpc.websocket(
    "/screencast",
    name="browser-screencast",
    namespace="browser.screencast",
    tags=("browser", "screencast"),
)


@browser_rpc.connection()
async def browser_connection(
    websocket: WebSocket,
    session_id: UUID,
    user: AuthenticatedUser = Depends(authenticated_user),
) -> BrowserConnection:
    return BrowserConnection(
        session_id=session_id,
        user_id=user.id,
    )
```

The variable is the grouping mechanism. Methods declared on `control` share
the control socket; declarations on `screencast` share the screencast socket.
The router-level connection factory is a default used by both channels. A
channel can override it when its authentication or context differs.

### Control socket

```python
@control.method()
async def key_down(
    params: KeyDownParams,
    browser: Inject[BrowserControl],
) -> None:
    await browser.key_down(params.key)


@control.method()
async def mouse_move(
    params: MouseMoveParams,
    browser: Inject[BrowserControl],
) -> None:
    await browser.mouse_move(params.x, params.y)


@control.event("state.changed", payload=BrowserStateChanged)
async def state_changes(
    events: Inject[BrowserControlEvents],
) -> AsyncIterator[BrowserStateChanged]:
    async for event in events:
        yield event
```

### Screencast socket

```python
@screencast.method()
async def start(
    params: StartScreencastParams,
    service: Inject[ScreencastService],
) -> ScreencastStarted:
    return await service.start(params)


@screencast.method()
async def stop(
    service: Inject[ScreencastService],
) -> None:
    await service.stop()


@screencast.event("frame", payload=ScreencastFrame)
async def frames(
    stream: Inject[ScreencastStream],
) -> AsyncIterator[ScreencastFrame]:
    async for frame in stream:
        yield frame
```

Registration should look like normal FastAPI registration:

```python
app = FastAPI()
app.include_router(browser_rpc)
```

There is no manually written `@app.websocket(...)` wrapper and no separate
`RpcWebSocketApp.serve(...)` call in normal application code.

## Why this shape

The important unit is not an individual method and not the complete router. It
is the shared WebSocket channel:

```text
browser_rpc
├── /control
│   ├── connection
│   ├── key_down
│   ├── mouse_move
│   └── state.changed
└── /screencast
    ├── connection
    ├── start
    ├── stop
    └── frame
```

This gives method-level locality without repeating endpoint metadata:

```python
@control.method()
async def key_down(...): ...

@screencast.method()
async def start(...): ...
```

The decorator immediately identifies the socket. A method cannot accidentally
inherit a router-wide `server=` value that is far away from its declaration.

## Generalization check

The browser example is only the multi-channel case. The same model must stay
small for simpler applications and leave room for reuse and other transports.

### Small application with one socket

The smallest useful application should not require a connection factory,
events, an application wrapper, or endpoint objects on every method:

```python
rpc = RpcAPIRouter(resolver=resolver)
main = rpc.websocket("/rpc", name="main")


@main.method()
async def ping() -> str:
    return "pong"


app.include_router(rpc)
```

If no `connection()` function is declared, the channel simply has no custom
typed context.

### Multiple sockets with shared connection setup

Path parameters, authentication, and connection context are commonly shared
by all channels under a router prefix. The router-level default avoids
repeating the same function:

```python
rpc = RpcAPIRouter(prefix="/projects/{project_id}", resolver=resolver)


@rpc.connection()
async def project_connection(
    project_id: UUID,
    user: User = Depends(current_user),
) -> ProjectConnection:
    return ProjectConnection(project_id=project_id, user_id=user.id)


commands = rpc.websocket("/commands", name="commands")
updates = rpc.websocket("/updates", name="updates")
```

The function runs once for each actual WebSocket connection. It is a shared
declaration default, not a shared runtime connection.

### A channel with different authentication or context

A channel-level factory overrides the router default locally:

```python
admin = rpc.websocket("/admin", name="admin")


@admin.connection()
async def admin_connection(
    project_id: UUID,
    admin_user: Admin = Depends(current_admin),
) -> AdminConnection:
    return AdminConnection(
        project_id=project_id,
        admin_id=admin_user.id,
    )
```

Exceptional policy therefore stays beside the exceptional channel.

### Request-only and event-only channels

Neither methods nor events should be mandatory. A command socket can contain
only methods; a telemetry socket can contain only events:

```python
commands = rpc.websocket("/commands", name="commands")
telemetry = rpc.websocket("/telemetry", name="telemetry")


@commands.method()
async def restart(service: Inject[Service]) -> None:
    await service.restart()


@telemetry.event("sample", payload=TelemetrySample)
async def samples(
    source: Inject[TelemetrySource],
) -> AsyncIterator[TelemetrySample]:
    async for sample in source:
        yield sample
```

### Reusable method groups

Direct channel decorators are the normal path. Endpoint-independent reuse can
remain an explicit lower-level feature rather than forcing indirection on
everyone:

```python
health = RpcModule(namespace="health")


@health.method()
async def ping() -> str:
    return "pong"


public.include(health)
admin.include(health)
```

`RpcModule` contains operations but no URL, connection handler, resolver, or
transport. Including it takes a snapshot into a channel. This preserves a
valid reuse case without returning to router-wide endpoint strings.

The name `RpcModule` is preferable to `RpcRouter` here because it cannot be
mistaken for a FastAPI router or a runtime endpoint.

### Transport-independent use

The operation collector behind a channel must live in the core package. The
FastAPI integration should adapt it rather than own dispatch semantics:

```python
worker = RpcChannel(name="worker", namespace="jobs")


@worker.method()
async def status(...) -> JobStatus:
    ...


server = worker.server(resolver=resolver)
response = await server.handle(decoded_json)
```

`RpcAPIRouter.websocket()` can return a FastAPI-bound `RpcChannel`. A channel
created directly has no URL and remains usable with custom transports. This
keeps the core transport-agnostic and prevents the FastAPI convenience API
from becoming the internal architecture.

### Configuration inheritance

Defaults should follow one predictable direction:

```text
RpcAPIRouter defaults
└── RpcChannel overrides
    └── method/event metadata
```

Good router defaults are `prefix`, resolver, connection factory, and shared
tags. Good channel-owned values are path, name, namespace, connection override,
and `resolver_scope`. A method owns only operation-specific metadata such as
RPC name, summary, declared errors, and payload/result types.

Endpoint/URL configuration should never be accepted by `method()` or
`event()`. The decorator owner already communicates the channel.

### General design constraints

The design is general enough only if all of these remain true:

- the one-socket application stays approximately five lines beyond its
  handler;
- multi-socket applications visibly group operations by connection;
- shared authentication can be declared once but overridden locally;
- methods, events, and connection context are independently optional;
- reusable operations do not need a fake URL;
- custom transports can consume the same channel protocol;
- FastAPI paths and generated-client paths come from one declaration;
- adding a second transport does not require changing method signatures;
- advanced reuse does not add ceremony to the common direct-decorator path.

## URL and generated-client configuration

The server declaration should contain only the real FastAPI path. Deployment
information such as scheme and host should be supplied when rendering the
contract, not repeated on each channel:

```python
contract = browser_rpc.contract(
    title="Browser RPC",
    public_base_url="wss://api.example.com",
)
```

The resulting generated endpoints are derived from:

```text
wss://api.example.com
    + /sessions/{sessionId}
    + /control or /screencast
```

For another environment, contract generation can use another base URL without
changing method declarations:

```python
contract = browser_rpc.contract(
    title="Browser RPC",
    public_base_url="wss://staging.example.com",
)
```

If generated clients should choose the host at runtime, the contract can leave
the base URL configurable while retaining the generated channel paths.

## Runtime ownership

Each channel must produce its own protocol/runtime view. Connecting to
`/control` exposes only `control.method()` declarations and starts only
`control.event()` sources. Connecting to `/screencast` does the same for the
screencast declarations.

This fixes the current mismatch: today `server=` guides generated clients, but
`RpcWebSocketApp` still serves every method and notification in its `RpcApp`.
The new channel must be both contract metadata and an enforced runtime
boundary.

## Dishka

The router owns the root resolver, while each channel keeps the existing scope
semantics:

```text
Dishka APP
└── WebSocket channel connection: SESSION
    ├── method invocation: REQUEST
    ├── method invocation: REQUEST
    └── event sources: SESSION
```

The value returned by `@channel.connection()` becomes connection context and
is injectable into methods and events. FastAPI resolves the connection
function's path parameters and `Depends(...)` values before the Dishka session
scope is entered.

## Naming

Recommended public vocabulary:

- `RpcAPIRouter`: the FastAPI-compatible collection of channels;
- `router.websocket(...)`: create and register one shared WebSocket channel;
- `channel.connection()`: declare authentication and connection context;
- `channel.method()`: declare a JSON-RPC request method;
- `channel.event()`: declare a server-pushed event stream.

Internally, events are still encoded as JSON-RPC notifications. The public API
does not need to expose that wire-level term.

## Clean break

The implementation should remove the split configuration rather than support
both designs:

```python
# Remove this model.
RpcRouter(server="browser-control")
OpenRpcContract(servers=(OpenRpcServer(...),))

# Remove the normal-use manual endpoint wrapper.
@fastapi_router.websocket("/control")
async def endpoint(websocket: WebSocket):
    await RpcWebSocketApp(...).serve(websocket)
```

The new source of truth is simply:

```python
control = browser_rpc.websocket("/control", ...)


@control.method()
async def key_down(...): ...
```

This is close to FastAPI, keeps methods that share a socket visibly grouped,
and avoids passing endpoint objects through every decorator.
