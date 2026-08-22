# pyrpckit

Decorator-driven, transport-agnostic [JSON-RPC 2.0](https://www.jsonrpc.org/specification)
protocols for Python.

Declare your API once on plain handler classes with Pydantic models. `pyrpckit`
derives the protocol from those declarations, validates and dispatches incoming
requests against it, and renders the same definition as JSON Schema and OpenRPC
so clients can be generated from it.

## Declaring handlers

```python
from enum import StrEnum

import pyrpckit as rpc
from pydantic import BaseModel


class AutomationRpcMethod(StrEnum):
    LIST = "automation.list"
    GET = "automation.get"


class GetAutomationParams(BaseModel):
    automation_id: str


class AutomationResponse(BaseModel):
    id: str
    name: str


class AutomationNotFound(rpc.RpcError):
    code = -32004
    message = "Automation not found"


class AutomationRpcMethods(rpc.RpcHandler):
    def __init__(self, service: AutomationService) -> None:
        self._service = service

    @rpc.method(AutomationRpcMethod.GET, errors=(AutomationNotFound,))
    async def get_automation(self, params: GetAutomationParams) -> AutomationResponse:
        """Get an automation."""
        job = await self._service.get(params.automation_id)
        return AutomationResponse(id=job.id, name=job.name)
```

Handler classes inherit from `RpcHandler`. A decorated method must accept exactly
`self` and one Pydantic params model, and must annotate its return type with a
Pydantic model or `None`. Violations are reported as a
`ProtocolDefinitionError` when the protocol is assembled — never at request time.

The `summary` is optional: without one, the first line of the docstring is used,
and a method with neither simply carries no summary into the generated contract.

## Assembling the protocol

Group handlers into features, then combine the features into one protocol. The
feature name tags its methods in the generated contract:

```python
AUTOMATION = rpc.feature("automation", handlers=(AutomationRpcMethods,))

protocol = rpc.RpcProtocol(AUTOMATION, version=1)
```

Features are worth it once the API has more than one area to group. For a small
protocol, or for a quick round-trip test, assemble one straight from the handler
classes:

```python
protocol = rpc.RpcProtocol.of(AutomationRpcMethods, version=1)
```

## Serving requests

`RpcServer` turns a decoded JSON payload into a response envelope, so it fits any
transport — WebSocket, HTTP, stdio, a message queue:

```python
server = rpc.RpcServer(AutomationRpcMethods(service))

response = await server.handle(await socket.receive_json())
if response is not None:
    await socket.send_json(response.model_dump(mode="json"))
```

The protocol is derived from the handlers you pass, so nothing is registered
twice. Pass `protocol=` to serve one you assembled yourself; the server then
checks the two against each other.

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


server = rpc.RpcServer(AutomationRpcMethods(service), error_mapper=to_rpc_error)
```

Anything neither declared nor mapped becomes an internal error, so handler
internals never leak to clients.

`RpcDispatcher` is available if you would rather build responses yourself: it
exposes `parse_request` and `execute` and raises the errors above.

## Server-initiated notifications

Notifications carry a payload that is either a decorated event model or a union of
them. Each event pins a `type` field to a literal, so clients can narrow the
union — and that literal is the event name:

```python
@rpc.event
class AutomationStarted(BaseModel):
    type: Literal["automation.started"] = "automation.started"
    automation_id: str


type AutomationEvent = AutomationStarted | AutomationFinished

AUTOMATION = rpc.feature(
    "automation",
    handlers=(AutomationRpcMethods,),
    notifications=(
        rpc.notification(
            "automation.event",
            AutomationEvent,
            summary="Publish an automation lifecycle event.",
        ),
    ),
)
```

Send one with the `RpcNotification` envelope.

## Generating the contract

```python
from pyrpckit.schema import render_json_schema, render_openrpc

render_json_schema(protocol, title="Automation Protocol")
render_openrpc(
    protocol,
    title="Automation",
    servers=({"name": "local", "url": "ws://127.0.0.1:8000/rpc"},),
)
```

The JSON Schema document lists every frame on the wire — one request schema per
method, the success and failure envelopes, and one envelope per notification —
under a single `oneOf`, and indexes the protocol in `x-rpc-methods`,
`x-rpc-notifications`, and `x-rpc-events`. The OpenRPC document describes the same
methods with their summaries and declared errors, and tags each one with the
feature it came from.

## Generating a client

The OpenRPC document is the input to the client generator. It writes a typed,
ready-to-use package into the repository that consumes the API:

```bash
pyrpckit generate python schema/greeting.openrpc.json --output src/greeting_client
```

The generated package holds no hand-written code and is meant to be committed:

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
client is constructed with anything satisfying the `pyrpckit.client.RpcTransport`
protocol, so it works over a WebSocket, HTTP, or a queue.

Run the generator with `--check` in CI to fail the build when the committed
client no longer matches the server schema:

```bash
pyrpckit generate python schema/greeting.openrpc.json --output src/greeting_client --check
```

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
