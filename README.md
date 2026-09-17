# pyrpckit

Build typed, transport-independent JSON-RPC 2.0 services in Python. A single
definition drives request dispatch, server events, binary streams, OpenRPC, and
generated Python or TypeScript clients.

## Install

```bash
uv add pyrpckit
uv add "pyrpckit[fastapi]"  # optional FastAPI adapter
uv add "pyrpckit[codegen]"  # optional client generation
```

Python 3.12 or newer is required.

## Define and test a service

Channels group related operations and provide their default RPC namespace.
Mount one or more channels on a service socket.

```python
from dataclasses import dataclass

from pyrpckit import Inject, RpcChannel, RpcModel, RpcService
from pyrpckit.testing import RpcTestClient


class GreetParams(RpcModel):
    name: str


class Greeting(RpcModel):
    text: str


@dataclass(frozen=True)
class Greeter:
    salutation: str


greeting = RpcChannel("greeting")


@greeting.method()
async def say(params: GreetParams, greeter: Inject[Greeter]) -> Greeting:
    return Greeting(text=f"{greeter.salutation}, {params.name}!")


app = RpcService(version=1)
app.socket("/rpc", greeting)


async def example() -> None:
    async with RpcTestClient(
        app, "/rpc", context={Greeter: Greeter("Hello")}
    ) as client:
        assert await client.request("greeting.say", {"name": "World"}) == {
            "text": "Hello, World!"
        }
```

`Inject[T]` is resolved on the server and never appears in the public request
schema. Methods are async free functions with either one request model or
direct named parameters, followed by injected dependencies. Use
`RpcChannel("name", namespace="")` for root-level operation names.

## Connections and events

A service-level `connect` hook runs before a socket is accepted. It may inspect
`RpcConnection`, resolve dependencies, and return one concrete value that is
then injectable for the connection lifetime. Raise `ConnectionRejected` to
reject the handshake deliberately.

```python
from collections.abc import AsyncIterator

from pyrpckit import Inject, RpcConnection, RpcModel, RpcService


class Session:
    pass


async def authenticate(connection: RpcConnection) -> Session:
    return Session()


class Progress(RpcModel):
    percent: int


@greeting.event(payload=Progress)
async def progress(session: Inject[Session]) -> AsyncIterator[Progress]:
    yield Progress(percent=100)


app = RpcService(connect=authenticate)
app.socket("/rpc", greeting)
```

The socket runtime owns acceptance, concurrent request handling, event tasks,
limits, shutdown, and connection-scoped cleanup. Custom transports implement
the exported `RpcSocket` protocol and call `app.serve(socket)`.

## Typed errors

Application errors have a stable string code, JSON-RPC integer code, default
message, and optionally typed details.

```python
from pyrpckit import RpcError, RpcModel


class MissingPageDetails(RpcModel):
    page_id: str


class MissingPage(RpcError):
    code = "page_missing"
    rpc_code = -32004
    message = "Page not found"
    details: MissingPageDetails


@greeting.method(raises=(MissingPage,))
async def open_page(page_id: str) -> None:
    raise MissingPage(MissingPageDetails(page_id=page_id))
```

The wire response keeps the JSON-RPC integer in `error.code` and places the
stable application code plus details in `error.data`. Declared errors are
written to OpenRPC and become concrete generated client exception classes.

## Binary streams

Binary streams are receive-only and use a separate socket from JSON-RPC.

```python
from collections.abc import AsyncIterator


@greeting.stream(content_type="audio/pcm;rate=24000")
async def audio(session: Inject[Session]) -> AsyncIterator[bytes]:
    yield b"..."


app.stream("/sessions/{session_id}/audio", audio)
```

Generated clients expose stream methods on the same namespace tree as RPC
methods. Await the result or use it as an async context manager, then call
`receive()` or iterate asynchronously. Generated WebSocket clients configure a
default opener; custom transports can pass their own stream opener. Stream
metadata is emitted as `x-rpckit-binary-streams` and generated into
`streams.py` or `streams.ts`.

## Contract and clients

The service is the source of endpoint URLs, protocol version, subprotocols,
and streams:

```python
contract = app.contract(
    title="Greeting API",
    base_url="wss://api.example.com",
)
```

Export and generate clients with one repository-relative configuration:

```toml
version = 1

[contract]
source = "my_api:contract"
output = "schema/openrpc.json"

[[clients]]
language = "python"
output = "src/greeting_client"
package = "greeting_client"
with_transport = "websocket"

[[clients]]
language = "typescript"
output = "frontend/src/greeting-client"
with_transport = "websocket"
```

```bash
pyrpckit generate --config rpcgen.toml
pyrpckit generate --config rpcgen.toml --check
```

Generators consume only OpenRPC. They understand pyrpckit's typed-error and
binary-stream extensions, but remain tolerant of ordinary OpenRPC tags from
external documents.

## FastAPI

```python
from fastapi import FastAPI

from pyrpckit.fastapi import create_router


web = FastAPI()
web.include_router(create_router(app))
```

`create_router()` adds every declared JSON-RPC and stream endpoint. Optional
context, resolver, error mapper, limits, prefix, and FastAPI dependencies can
be supplied once for the router. The core package itself has no web-framework
dependency.

## Development

```bash
uv sync --all-groups
uv run ruff check .
uv run ruff format .
uv run pytest
```
