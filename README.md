# pyrpckit

Build typed, bidirectional JSON-RPC 2.0 gateways with a declaration style that
feels familiar from FastAPI.

`pyrpckit` turns decorated async functions into a transport-agnostic protocol,
an OpenRPC contract, and generated Python or TypeScript clients. It is designed
for APIs where requests and server-initiated notifications share one long-lived
connection.

## Installation

```bash
uv add pyrpckit
```

Client generation, FastAPI, and Dishka are optional:

```bash
uv add "pyrpckit[codegen]"
uv add "pyrpckit[fastapi,dishka]"
```

Python 3.12 or newer is required.

## Declare methods

Handlers are async free functions. A normal parameter belongs to the JSON-RPC
contract; `Inject[T]` is resolved only on the server and is absent from OpenRPC.

```python
from pyrpckit import Inject, RpcApp, RpcModel, RpcRouter


class NavigateParams(RpcModel):
    url: str


navigation_rpc = RpcRouter(
    namespace="navigation",
    tags=("browser",),
)


@navigation_rpc.method()
async def navigate(
    params: NavigateParams,
    navigation: Inject[BrowserNavigation],
) -> None:
    await navigation.navigate(params.url)
```

The parentheses are intentional: methods are always declared through a
decorator factory, leaving a consistent place for options.

```python
@navigation_rpc.method(
    "history.back",
    errors=(NavigationUnavailable,),
)
async def back(
    navigation: Inject[BrowserNavigation],
) -> None:
    await navigation.back()
```

A method may use one positional Pydantic params model. Methods without wire
parameters may omit it, and either form may additionally use injected parameters.

`RpcModel` applies strict input and camel-case wire aliases. Plain Pydantic
models are adapted at the protocol boundary as well.

## Compose an application

Routers compose directly into an application:

```python
browser_rpc = RpcApp()

browser_rpc.include_router(clipboard_rpc)
browser_rpc.include_router(navigation_rpc)
```

An include takes a snapshot. It may add a namespace or tags:

```python
browser_rpc.include_router(
    internal_rpc,
    namespace="internal",
    tags=("admin",),
)
```

There are no controller instances, constructor injection, mount bindings, or
`bind(...)` step.

## Dependency resolution and call scopes

PyRPC Kit owns the injection syntax and scope timing, but not a DI container.
A resolver only needs one method:

```python
from typing import Protocol


class RpcResolver(Protocol):
    async def resolve[T](self, dependency: type[T]) -> T: ...
```

Create a transport-agnostic server with a resolver:

```python
server = browser_rpc.server(resolver=resolver)
response = await server.handle(decoded_json)
```

The default `call_scope` is entered once per RPC invocation, including each
member of a batch. A resolver may implement `enter_scope()` as an async context
manager to create and clean up its call-scoped child. Most integrations,
including Dishka, need no additional configuration.

Advanced integrations can replace that behavior for a router or a specific
mount with a custom `RpcResolverScope` callable:

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from pyrpckit import RpcResolver, call_scope


@asynccontextmanager
async def traced_scope(resolver: RpcResolver) -> AsyncIterator[RpcResolver]:
    async with call_scope(resolver) as scoped_resolver:
        # Start tracing or another invocation-specific resource here.
        yield scoped_resolver


browser_rpc.include_router(internal_rpc, resolver_scope=traced_scope)
```

Connection context is also a dependency. A WebSocket endpoint can pass a typed
value without placing connection state on handlers:

```python
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class SessionConnection:
    session_id: UUID
    user_id: UUID
```

Do not retain call-scoped dependencies in background jobs. A method that starts
long-lived work should hand it to an application service that owns the job
lifetime and its own scope.

## Typed notifications

A notification is an injected async source declared directly on its router:

```python
from collections.abc import AsyncIterator
from typing import Literal

from pyrpckit import Inject


class SessionUpdated(RpcModel):
    type: Literal["session.updated"] = "session.updated"
    revision: int


@session_rpc.notification(
    "event",
    payload=SessionUpdated,
    summary="Canonical session update.",
)
async def session_notifications(
    events: Inject[SessionEvents],
    connection: Inject[SessionConnection],
) -> AsyncIterator[SessionUpdated]:
    async with events.subscribe(connection.session_id) as stream:
        async for event in stream:
            yield event
```

The router owns the fully qualified method name and the declared payload. The
WebSocket runtime starts every included source once per connection, resolves
its injected dependencies from the connection scope, validates every yielded
payload, and wraps it in a JSON-RPC notification. Payload unions with literal
`type` fields are exported as discriminated notification types for generated
clients.

The complete session migration, including Dishka, application composition, and
FastAPI router registration, is shown in
[`docs/session_rpc_new_api.py`](docs/session_rpc_new_api.py).

## FastAPI WebSockets

`pyrpckit.fastapi` provides a runtime facade. The endpoint contains no transport
loop:

```python
from fastapi import APIRouter, WebSocket
from pyrpckit.fastapi import RpcWebSocketApp


SESSION_RPC_APP = RpcWebSocketApp(
    session_rpc_app,
    resolver=resolver,
    max_concurrency=32,
    max_queue_size=128,
)

router = APIRouter(prefix="/sessions")


@router.websocket("/{session_id}/rpc")
async def session_rpc_endpoint(
    websocket: WebSocket,
    session_id: UUID,
    user_id: AuthenticatedUserId,
) -> None:
    await SESSION_RPC_APP.serve(
        websocket,
        context=SessionConnection(session_id=session_id, user_id=user_id),
    )
```

The facade accepts the socket, decodes and encodes JSON, handles batches and
parse errors, bounds concurrent calls and the outgoing queue, serializes writes,
and multiplexes responses with typed notifications.

## Dishka

The optional adapter maps a WebSocket connection to Dishka `SESSION` scope and
each RPC invocation to its child `REQUEST` scope:

```python
from pyrpckit.dishka import DishkaResolver
from pyrpckit.fastapi import RpcWebSocketApp


SESSION_RPC_APP = RpcWebSocketApp(
    session_rpc_app,
    resolver=DishkaResolver(container),
)
```

The value passed to `serve(..., context=value)` is available directly through
`Inject[type(value)]` and is also forwarded to Dishka's connection context map.
The core package imports neither FastAPI nor Dishka.

## Errors and JSON-RPC messages

Declare application errors on methods and raise them from handlers:

```python
class SessionNotFound(RpcError):
    code = -32004
    message = "Session not found"


@session_rpc.method(errors=(SessionNotFound,))
async def sync(...) -> SessionSnapshot:
    ...
```

`RpcServer.handle(...)` accepts a decoded request or batch.
`RpcServer.handle_json(...)` centralizes JSON decoding and encoding, including
parse errors. Invalid envelopes, unknown methods, invalid params, and internal
errors become JSON-RPC failure responses; notifications do not receive a
response.

## OpenRPC and generated clients

The contract remains the boundary for code generation. Injected parameters and
server runtime details do not change it.

```python
from pyrpckit import OpenRpcContract


CONTRACT = OpenRpcContract(
    app=browser_rpc,
    title="Browser API",
)
```

```bash
pyrpckit schema browser.api:CONTRACT --output schema/browser.openrpc.json
pyrpckit generate schema/browser.openrpc.json \
  --language python \
  --output src/browser_client \
  --package browser_client \
  --client-name BrowserClient
```

TypeScript generation uses the same document with `--language typescript`.
Generated clients retain the namespace-oriented API and transport abstraction.

## Development

```bash
uv sync --all-groups
uv run ruff check .
uv run ruff format .
uv run pytest
```
