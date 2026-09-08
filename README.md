# pyrpckit

Decorator-driven, transport-agnostic [JSON-RPC 2.0](https://www.jsonrpc.org/specification)
protocols for Python.

Declare methods on local routers, compose them into one `RpcApp`, and bind normal
Python objects when the application starts. `pyrpckit` validates and dispatches
incoming requests against that declaration and renders the same protocol as an
OpenRPC contract for client generation.

## Declaring routes

```python
import pyrpckit as rpc
from pydantic import BaseModel


class GetAutomationParams(BaseModel):
    automation_id: str


class AutomationResponse(BaseModel):
    id: str
    name: str


class AutomationNotFound(rpc.RpcError):
    code = -32004
    message = "Automation not found"


router = rpc.RpcRouter(prefix="automation", tags=("automation",))


class AutomationRpcMethods:
    def __init__(self, service: AutomationService) -> None:
        self._service = service

    @router.method("get", errors=(AutomationNotFound,))
    async def get_automation(self, params: GetAutomationParams) -> AutomationResponse:
        """Get an automation."""
        job = await self._service.get(params.automation_id)
        return AutomationResponse(id=job.id, name=job.name)
```

The prefix supplies the JSON-RPC namespace once, while tags group methods in the
OpenRPC document. Handler classes need no base class. A decorated method accepts
`self` and at most one Pydantic params model, and must annotate its return type
with a Pydantic model or `None`. Invalid declarations raise
`ProtocolDefinitionError` when the app builds its protocol.

A method that needs nothing from the caller simply leaves the params out, and one
that answers with nothing returns `None` — no placeholder models:

```python
@router.method("list")
async def list_automations(self) -> AutomationListResponse: ...


@router.method("cancel_all")
async def cancel_all(self) -> None: ...
```

Both stay OpenRPC conformant: such a method is described with `"params": []` and a
`null` result schema, its request may omit `params` entirely, and the generated
client exposes it as `await client.automation.cancel_all()`.

The `summary` is optional: without one, the first line of the docstring is used,
and a method with neither simply carries no summary into the generated contract.

Free functions use the same decorator and need no runtime binding:

```python
utility_router = rpc.RpcRouter()


@utility_router.method("ping")
async def ping() -> None:
    pass
```

## Composing the app

Include routers once to create the complete API definition:

```python
app = rpc.RpcApp(version=1)
app.include_router(router)
app.include_router(utility_router)
```

An include takes a snapshot. An optional include prefix is prepended to the
router prefix with a dot, and include tags are appended with ordered
deduplication. Accessing `app.protocol` validates and freezes the composition.

## Serving requests

`RpcServer` turns a decoded JSON payload into a response envelope, so it fits any
transport — WebSocket, HTTP, stdio, a message queue:

```python
server = app.bind(AutomationRpcMethods(service))

response = await server.handle(await socket.receive_json())
if response is not None:
    await socket.send_json(response.model_dump(mode="json"))
```

`app.bind(...)` verifies that every declared instance method has exactly one
matching handler and rejects decorated methods from routers the app does not
contain. Free functions are already bound and require no argument.

The same router can be mounted under multiple prefixes. One instance normally
serves every mount; bind mounts explicitly when they need different state:

```python
mounted_app = rpc.RpcApp()
primary = mounted_app.include_router(router, prefix="primary")
secondary = mounted_app.include_router(router, prefix="secondary")

server = mounted_app.bind(
    primary.bind(AutomationRpcMethods(primary_service)),
    secondary.bind(AutomationRpcMethods(secondary_service)),
)
```

Unknown methods, malformed envelopes, and invalid params become the matching
JSON-RPC failures.

## Reporting errors

A handler reports a failure by raising an `RpcError` subclass. It goes on the wire
with the code and message it declares — the same class the method lists in
`errors=`, so the contract and the implementation cannot drift:

```python
raise AutomationNotFound(f"No automation {params.automation_id}")
```

Exceptions you cannot make into an `RpcError` — from a library, say — are
translated by an optional `error_mapper`:

```python
def to_rpc_error(error: Exception) -> rpc.RpcError | None:
    if isinstance(error, HttpxTimeout):
        return rpc.RpcError("Upstream timed out", code=-32005)
    return None


server = app.bind(AutomationRpcMethods(service), error_mapper=to_rpc_error)
```

Anything neither declared nor mapped becomes an internal error, so handler
internals never leak to clients.

## Server-initiated events

Events carry a payload that is either a decorated event model or a union of them.
Each event pins a `type` field to a literal, so clients can narrow the union — and
that literal is the event name. On the JSON-RPC wire, an event is a notification
without an `id`:

```python
@rpc.event
class AutomationStarted(BaseModel):
    type: Literal["automation.started"] = "automation.started"
    automation_id: str


type AutomationEvent = AutomationStarted | AutomationFinished

events = rpc.RpcRouter(prefix="automation", tags=("automation",))
events.event(
    "event",
    AutomationEvent,
    summary="Publish an automation lifecycle event.",
)
app.include_router(events)
```

Send one with the `RpcNotification` envelope.

## Generating the contract

The contract is a build-time artefact, so no running server is involved. For a
deployment-aware contract, pair the app with typed OpenRPC server metadata:

```python
CONTRACT = rpc.OpenRpcContract(
    app=app,
    title="Automation",
    servers=(
        rpc.OpenRpcServer(
            name="production",
            url="wss://{host}/automation/rpc",
            variables={
                "host": rpc.ServerVariable(default="api.example.com"),
            },
            extensions={
                "x-rpckit-transport": {
                    "type": "websocket",
                    "messageEncoding": "json",
                }
            },
        ),
    ),
)
```

Name the contract as `module:attribute`, the way uvicorn names an app:

```bash
pyrpckit schema automation.api:CONTRACT --output schema/automation.openrpc.json
```

Pass `--check` in CI to fail the build when the committed contract no longer
matches the decorated API.

The document is available as a plain function too:

```python
from pyrpckit.schema import render_openrpc

render_openrpc(
    app.protocol,
    title="Automation",
    servers=({"name": "local", "url": "ws://127.0.0.1:8000/rpc"},),
)
```

The OpenRPC document describes every method with its parameters, result,
summaries, declared errors, and router tags. Its JSON Schema components
also describe the request and notification envelopes used by client generation.

## Generating a client

The OpenRPC document is the input to the client generator. It writes a typed,
ready-to-use package into the repository that consumes the API:

```bash
pyrpckit generate python schema/greeting.openrpc.json --output src/greeting_client
```

TypeScript clients use the same OpenRPC input and language-neutral IR:

```bash
pyrpckit generate typescript schema/greeting.openrpc.json \
  --output src/generated \
  --client-name GreetingClient \
  --transport-module ../transport
```

This writes `models.ts`, `client.ts`, and `index.ts`. The transport module stays
outside the generated directory and exports this transport-agnostic contract:

```typescript
export interface RpcTransport {
  request<TResult>(method: string, params?: object): Promise<TResult>;
  notifications(): AsyncIterable<unknown>;
  close(): Promise<void>;
}
```

The generated Python package holds no hand-written code and is meant to be committed:

- `models.py` — every schema as a Pydantic model, plus an `RpcMethod` enum
- `namespaces/<name>.py` — one class per method prefix, one typed `async def` per method
- `client.py` — the facade that wires the namespaces together, plus the typed
  notification stream
- `__init__.py` — the package exports

```python
async with GreetingClient(transport) as client:
    greeting = await client.greeting.say(name="Mathis")  # -> SayResult
    async for notification in client.notifications():  # -> GreetingChangedNotification
        print(notification.params)
```

Only the schemas the client actually reaches are emitted — request and response
envelopes stay out of the generated models. The transport is not generated: the
Python clients accept anything satisfying `pyrpckit.client.RpcTransport`, while
TypeScript clients import the equivalent `RpcTransport` interface from
`--transport-module`. Both therefore work over a WebSocket, HTTP, or a queue.

Run the generator with `--check` in CI to fail the build when the committed
client no longer matches the server schema:

```bash
pyrpckit generate python schema/greeting.openrpc.json --output src/greeting_client --check
```

Use `typescript` instead of `python` in the same command to check generated
TypeScript files.

## Development

Small, direct library examples live in [`examples/`](examples/). For a complete,
runnable server-to-generated-client walkthrough, see the
[FastAPI showcase](showcase/README.md).

This project uses [uv](https://docs.astral.sh/uv/) for dependency management.

```bash
uv sync --all-groups
uv run pre-commit install
uv run ruff check .
uv run ruff format .
uv run pytest
```
