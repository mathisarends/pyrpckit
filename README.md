# pyrpckit

**Build typed, bidirectional gateways with the ergonomics of a Python web
framework.**

`pyrpckit` turns decorated Python handlers into a transport-agnostic
[JSON-RPC 2.0](https://www.jsonrpc.org/specification) protocol, an OpenRPC
contract, and generated Python or TypeScript clients.

It is useful when an API is more than a collection of HTTP endpoints: a client
starts work, the server streams typed updates, and the client can steer or cancel
that work while it is running. Agent gateways are a natural example—think text
deltas, tool calls, tool results, lifecycle changes, and user steering over one
long-lived connection.

The architecture takes inspiration from gateway protocols used by systems such
as OpenClaw and OpenAI's
[Codex App Server](https://developers.openai.com/codex/app-server), while the
declaration style should feel familiar to FastAPI users. `pyrpckit` is not tied
to either project and does not claim protocol compatibility with them.

```text
                         build time
  decorated protocol -----------------> OpenRPC contract
          |                                      |
          |                              client generation
          |                             /                 \
       RpcServer                 typed Python       typed TypeScript
          ^                             clients           clients
          |                                \               /
          +---- requests / responses ------- transport ---+
          +---- typed server events -------- transport --->
```

## Contents

- [Why pyrpckit?](#why-pyrpckit)
- [What you can build](#what-you-can-build)
- [How it fits together](#how-it-fits-together)
- [Installation](#installation)
- [A bidirectional gateway in one protocol](#a-bidirectional-gateway-in-one-protocol)
- [Declaring routes](#declaring-routes)
- [Composing the app](#composing-the-app)
- [Serving requests](#serving-requests)
- [Reporting errors](#reporting-errors)
- [Server-initiated events](#server-initiated-events)
- [Generating the contract](#generating-the-contract)
- [Generating a client](#generating-a-client)
- [Development](#development)

## Why pyrpckit?

OpenAPI-based HTTP clients are excellent for request/response APIs. They become
less helpful when part of the real API lives on a WebSocket or another streaming
transport. Event payload types often need to be exported separately, socket
routes are written by hand, and the generated client knows nothing about the
messages arriving from the server.

`pyrpckit` describes both halves as one protocol:

- **Client-to-server methods** are validated, dispatched, documented, and
  generated as typed client methods.
- **Server-to-client events** are declared alongside those methods and generated
  as typed notification streams.
- **Discriminated event unions** let clients safely narrow `text.delta`,
  `tool.call`, `tool.result`, and other payloads.
- **The transport is an adapter.** Use a WebSocket, HTTP, stdio, a message queue,
  an IPC channel, or something custom.
- **The OpenRPC document is the boundary.** Generators consume the contract, not
  the live Python application.

The result is one source of truth for validation, discovery, generated types,
method names, results, events, and declared errors—without turning the library
into a web framework.

## What you can build

| Use case | Methods flowing in | Events flowing out |
| --- | --- | --- |
| Agent gateway | start, steer, approve, cancel | text deltas, tool calls, tool results, completion |
| Automation control plane | launch, pause, retry | progress, logs, state transitions |
| Remote browser or device control | navigate, click, inspect | DOM changes, screenshots, telemetry |
| Developer tooling | run, debug, stop | diagnostics, output, test results |
| Realtime application backend | commands and queries | domain events and live updates |

These are architectural patterns, not bundled transports or domain-specific
implementations. `pyrpckit` supplies the typed protocol layer between them.

## How it fits together

The programming model has four small pieces:

1. A `RpcRouter` groups methods and server-initiated events by namespace.
2. A `RpcApp` composes routers into one validated protocol.
3. A bound `RpcServer` validates and dispatches decoded JSON-RPC messages.
4. An OpenRPC contract generates clients that depend only on a tiny transport
   interface.

This separation matters for gateways: protocol code stays stable while the
connection strategy—WebSocket, queue, local process, or otherwise—can change per
deployment.

## Installation

`pyrpckit` requires Python 3.12 or newer. Add it to a project with
[`uv`](https://docs.astral.sh/uv/):

```bash
uv add pyrpckit
```

This adds `pyrpckit` to the project's `pyproject.toml`, updates the lockfile,
and installs it into the project's environment.

With `pip`:

```bash
python -m pip install pyrpckit
```

The runtime dependency on Pydantic is installed automatically. No web framework
or transport dependency is included.

## A bidirectional gateway in one protocol

An agent run makes the two directions concrete. Commands enter the gateway while
typed updates leave it:

```python
from typing import Literal

import pyrpckit as rpc


class StartRunParams(rpc.RpcModel):
    prompt: str


class RunRef(rpc.RpcModel):
    run_id: str


class SteerRunParams(rpc.RpcModel):
    run_id: str
    instruction: str


@rpc.event
class TextDelta(rpc.RpcModel):
    type: Literal["text.delta"] = "text.delta"
    run_id: str
    delta: str


@rpc.event
class ToolCall(rpc.RpcModel):
    type: Literal["tool.call"] = "tool.call"
    run_id: str
    call_id: str
    name: str
    arguments: dict[str, object]


@rpc.event
class ToolResult(rpc.RpcModel):
    type: Literal["tool.result"] = "tool.result"
    run_id: str
    call_id: str
    output: str


type AgentEvent = TextDelta | ToolCall | ToolResult

agent = rpc.RpcRouter(prefix="agent", tags=("agent",))


class AgentMethods:
    def __init__(self, service: AgentService) -> None:
        self._service = service

    @agent.method("run.start")
    async def start(self, params: StartRunParams) -> RunRef:
        run_id = await self._service.start(params.prompt)
        return RunRef(run_id=run_id)

    @agent.method("run.steer")
    async def steer(self, params: SteerRunParams) -> None:
        await self._service.steer(params.run_id, params.instruction)


agent.event("event", AgentEvent, summary="Stream updates from an agent run.")

app = rpc.RpcApp(version=1)
app.include_router(agent)
server = app.bind(AgentMethods(service))
```

The protocol now contains `agent.run.start`, `agent.run.steer`, and the
`agent.event` notification. A generated TypeScript client makes all of them
discoverable:

```typescript
const run = await client.agent.run.start({
  prompt: "Investigate the deployment failure",
});

// This can be triggered while the event stream is still active.
await client.agent.run.steer({
  runId: run.runId,
  instruction: "Check the logs first",
});

for await (const notification of client.notifications()) {
  const event = notification.params;

  switch (event.type) {
    case "text.delta":
      renderText(event.delta);
      break;
    case "tool.call":
      showPendingTool(event.name, event.arguments);
      break;
    case "tool.result":
      showToolResult(event.callId, event.output);
      break;
  }
}
```

The event `type` literals become a discriminated union in generated clients.
There is no second set of handwritten socket payload types to keep in sync.

## Declaring routes

```python
from pydantic import BaseModel

import pyrpckit as rpc


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

    @router.method(errors=(AutomationNotFound,))
    async def get(self, params: GetAutomationParams) -> AutomationResponse:
        """Get an automation."""
        job = await self._service.get(params.automation_id)
        return AutomationResponse(id=job.id, name=job.name)
```

The prefix supplies the JSON-RPC namespace once, while tags group methods in the
OpenRPC document. Handler classes need no base class. The bare `@router.method`
form uses the Python function name, so this method is exposed as `automation.get`.

Request and response classes may be ordinary Pydantic models. The router adapts
them at the protocol boundary without modifying the classes: Python keeps
`snake_case`, while OpenRPC and JSON use `camelCase`, explicit Pydantic aliases win,
and unknown input fields are rejected. `RpcModel` remains available when the same
convention is also useful outside a decorated handler.

Small methods may declare named parameters directly. Keyword-only arguments become
JSON-RPC parameters, and any supported annotated result type becomes the result
schema:

```python
@router.method
async def search(*, query: str, max_results: int = 10) -> list[str]:
    return await service.search(query, limit=max_results)
```

Use the callable form when the method has options. An explicit first argument is an
optional wire-name override, not required boilerplate:

```python
@router.method(errors=(AutomationNotFound,))
async def get(...) -> AutomationResponse: ...


@router.method("get", errors=(AutomationNotFound,))
async def fetch_automation(...) -> AutomationResponse: ...
```

A method that needs nothing from the caller simply leaves the params out, and one
that answers with nothing returns `None` — no placeholder models:

```python
@router.method
async def list(self) -> AutomationListResponse: ...


@router.method
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


@utility_router.method
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
class AutomationStarted(rpc.RpcModel):
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
