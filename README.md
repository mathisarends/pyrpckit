# pyrpckit

Build a pleasant, typed JSON-RPC API in Python.

`pyrpckit` lets you describe an API with small async functions, then use that
same description to serve requests, publish an OpenRPC contract, and generate
typed clients. It stays out of the way of your transport and dependency
injection choices, so the API definition remains the easy part to read.

It is especially handy for WebSocket-style applications, where a client calls
methods and the server can also send typed events over the same connection.

## Contents

- [Install](#install)
- [Your first API](#your-first-api)
- [Use it from your application](#use-it-from-your-application)
- [Send typed events](#send-typed-events)
- [Create a contract and clients](#create-a-contract-and-clients)
- [FastAPI and Dishka](#fastapi-and-dishka)
- [Errors](#errors)
- [Development](#development)

## Install

```bash
uv add pyrpckit
```

Add extras only when you need them:

```bash
uv add "pyrpckit[codegen]"          # client generation
uv add "pyrpckit[fastapi,dishka]"   # WebSocket and Dishka helpers
```

Python 3.12 or newer is required.

## Your first API

Start with a WebSocket channel and a normal async function. Methods declared on
the same channel share one socket. `RpcModel` gives request and response data a
consistent JSON shape; its field names are automatically available in
camelCase on the wire.

```python
from fastapi import APIRouter, FastAPI, WebSocket

from pyrpckit import Inject, RpcChannel, RpcModel
from pyrpckit.fastapi import serve


class OpenPage(RpcModel):
    url: str


class Page(RpcModel):
    title: str
    url: str


router = APIRouter(prefix="/sessions/{session_id}")
browser = RpcChannel(
    name="browser-control",
    namespace="browser",
    tags=("navigation",),
)


@browser.method()
async def open_page(
    params: OpenPage,
    navigation: Inject[BrowserNavigation],
) -> Page:
    page = await navigation.open(params.url)
    return Page(title=page.title, url=page.url)


@router.websocket("/control")
async def control_endpoint(websocket: WebSocket) -> None:
    await serve(browser, websocket, resolver=resolver)


app = FastAPI()
app.include_router(router)
```

That is the API. Clients see a `browser.open_page` method that accepts
`OpenPage` and returns `Page`. Your application sees the `BrowserNavigation`
service it already knows how to provide.

`Inject[...]` marks a server-side dependency. It never becomes part of the
public JSON-RPC request or the generated contract. A handler can have one
positional `RpcModel` parameter (or none) plus any number of injected services.

You can choose a more descriptive wire name without changing the Python
function name:

```python
@browser.method("history.back")
async def go_back(navigation: Inject[BrowserNavigation]) -> None:
    await navigation.back()
```

## Use it from your application

For a custom transport, create the same channel directly. Pass known services
as typed `context`; add a `resolver` when dependencies need dynamic or scoped
resolution. The core library does not prescribe a web framework or DI container.

```python
from pyrpckit import RpcChannel


channel = RpcChannel(name="browser", namespace="browser")


@channel.method()
async def ping() -> str:
    return "pong"


server = channel.server(context={BrowserNavigation: navigation})
response = await server.handle_json(request_body)
```

`handle_json()` takes a JSON request (or batch) and returns JSON ready to send
back. If your transport already decoded the request, use `handle()` instead.

Each call gets its own dependency scope by default. This makes request-scoped
resources such as database sessions simple to clean up. Keep work that outlives
a call in a service designed to own that longer lifetime.

## Send typed events

For server-initiated updates, declare an event source alongside the methods it
belongs to. The WebSocket runtime starts it once per connection and
sends each yielded value as a JSON-RPC notification.

```python
from collections.abc import AsyncIterator
from typing import Literal


class PageChanged(RpcModel):
    type: Literal["page.changed"] = "page.changed"
    url: str


@browser.event("event", payload=PageChanged)
async def page_events(
    events: Inject[BrowserEvents],
) -> AsyncIterator[PageChanged]:
    async with events.subscribe() as stream:
        async for event in stream:
            yield PageChanged(url=event.url)
```

Event payloads are validated before they are sent and are included in
the generated client types. Literal `type` fields also become discriminated
event unions in supported clients.

## Create a contract and clients

The OpenRPC document is the portable description of your API. Declare public
WebSocket URLs explicitly, including any deployment prefixes. Keep them in sync
with your FastAPI endpoints; URL variable names are preserved exactly.

```python
from pyrpckit import RpcContract, ServerVariable


contract = RpcContract.from_channels(
    channels=[browser],
    title="Browser API",
    server_urls={
        "browser-control": "wss://api.example.com/sessions/{sessionId}/control",
    },
    variables={
        "sessionId": ServerVariable(default="demo-session"),
    },
)
```

Put the contract source and every generated client in one repository-relative
configuration:

```toml
# rpcgen.toml
version = 1

[contract]
source = "browser.api:contract"
output = "schema/browser.openrpc.json"

[[clients]]
language = "python"
output = "src/browser_client"
package = "browser_client"
client_name = "BrowserClient"

[[clients]]
language = "typescript"
output = "frontend/generated/browser-client"
client_name = "BrowserClient"
with_transport = "websocket"
```

Then update or verify the contract and all clients with the same command:

```bash
pyrpckit generate --config rpcgen.toml
pyrpckit generate --config rpcgen.toml --check
```

Each client may still name its own `schema` when it is generated from an
external OpenRPC document. Every generated TypeScript request, response, and
event model is exported from the package root, so consumers can import all
public types from the generated package entry point.

A generated single-server TypeScript client can override its deployed endpoint
with a string or `URL`. Declared WebSocket subprotocols remain in effect:

```typescript
const client = await BrowserClient.connect({ url: socketUrl(session.path) });
```

For multi-server contracts, pass `endpoints`. Each override needs only
`server` and `url`; `subprotocols` is optional and defaults to the contract.

## FastAPI and Dishka

Use a normal FastAPI router and one WebSocket endpoint per channel. FastAPI
resolves path parameters and dependencies before `serve()` accepts the socket.
Pass an object or a mapping of dependency types to values as `context`; these
values and the `WebSocket` are injectable into methods and events on that socket.
FastAPI owns cleanup of its dependencies, including dependencies using `yield`.

```python
from dataclasses import dataclass

from fastapi import APIRouter, Depends, FastAPI, WebSocket

from pyrpckit import Inject, RpcChannel
from pyrpckit.fastapi import serve


@dataclass(frozen=True)
class BrowserConnection:
    session_id: str
    user_id: str


router = APIRouter(prefix="/sessions/{session_id}")
control = RpcChannel(name="control", namespace="browser")


async def browser_connection(
    websocket: WebSocket,
    session_id: str,
    user: User = Depends(current_user),
) -> BrowserConnection:
    return BrowserConnection(session_id=session_id, user_id=user.id)


@control.method()
async def current_url(connection: Inject[BrowserConnection]) -> str:
    return await lookup_url(connection.session_id)


@router.websocket("/control")
async def control_endpoint(
    websocket: WebSocket,
    context: BrowserConnection = Depends(browser_connection),
) -> None:
    await serve(control, websocket, context=context, resolver=resolver)


app = FastAPI()
app.include_router(router)
```

If you use Dishka, pass its adapter as the resolver:

```python
from pyrpckit.dishka import DishkaResolver
from pyrpckit.fastapi import serve


@router.websocket("/dishka-control")
async def dishka_endpoint(websocket: WebSocket) -> None:
    await serve(control, websocket, resolver=DishkaResolver(container))
```

Each socket opens a Dishka `SESSION`; individual calls open `REQUEST` scopes.
The core package has no FastAPI or Dishka dependency.

`serve()` also accepts `error_mapper`, `max_concurrency` (default 32),
`max_queue_size` (default 128), and `subprotocol`. Call it on an unaccepted socket.
For contracts, declare matching subprotocols with
`RpcContract.from_channels(..., subprotocols={"control": "jsonrpc"})`.
Contract export freezes channel definitions; all channels must share a protocol
version. Configure tags and version directly on each `RpcChannel`.

Migrating from `RpcAPIRouter`: replace it with `APIRouter`, create channels with
`RpcChannel`, and register explicit endpoints calling `serve()`. Replace
`@router.connection()` / `@channel.connection()` with ordinary FastAPI dependencies
and pass their results as `context`. Replace `router.contract()` with
`RpcContract.from_channels()` and explicit `server_urls`.

## Errors

Declare the errors a caller can handle, then raise them naturally in the
handler:

```python
from pyrpckit import RpcError


class PageNotFound(RpcError):
    code = -32004
    message = "Page not found"


class PageId(RpcModel):
    id: str


@browser.method(errors=(PageNotFound,))
async def get_page(
    params: PageId,
    navigation: Inject[BrowserNavigation],
) -> Page:
    page = await navigation.get(params.id)
    if page is None:
        raise PageNotFound()
    return Page(title=page.title, url=page.url)
```

Known errors become clear JSON-RPC responses and are recorded in the contract.
Invalid requests, unknown methods, invalid parameters, and unexpected failures
are handled as standard JSON-RPC errors. Notifications do not receive a reply.

## Development

```bash
uv sync --all-groups
uv run ruff check .
uv run ruff format .
uv run pytest
```
