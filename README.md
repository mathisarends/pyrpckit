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
          +---- typed notifications -------- transport --->
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
- [Server-initiated notifications](#server-initiated-notifications)
- [Generating the contract](#generating-the-contract)
- [Generating a client](#generating-a-client)
- [Development](#development)

## Why pyrpckit?

OpenAPI-based HTTP clients are excellent for request/response APIs. They become
less helpful when part of the real API lives on a WebSocket or another streaming
transport. Notification payload types often need to be exported separately, socket
routes are written by hand, and the generated client knows nothing about the
messages arriving from the server.

`pyrpckit` describes both halves as one protocol:

- **Client-to-server methods** are validated, dispatched, documented, and
  generated as typed client methods.
- **Server-to-client notifications** are declared alongside those methods and
  generated as typed notification streams.
- **Discriminated notification unions** let clients safely narrow `text.delta`,
  `tool.call`, `tool.result`, and other payloads.
- **The transport is an adapter.** Use a WebSocket, HTTP, stdio, a message queue,
  an IPC channel, or something custom.
- **The OpenRPC document is the boundary.** Generators consume the contract, not
  the live Python application.

The result is one source of truth for validation, discovery, generated types,
method names, results, notifications, and declared errors—without turning the
library into a web framework.

## What you can build

| Use case                         | Methods flowing in            | Notifications flowing out                         |
| -------------------------------- | ----------------------------- | ------------------------------------------------- |
| Agent gateway                    | start, steer, approve, cancel | text deltas, tool calls, tool results, completion |
| Automation control plane         | launch, pause, retry          | progress, logs, state transitions                 |
| Remote browser or device control | navigate, click, inspect      | DOM changes, screenshots, telemetry               |
| Developer tooling                | run, debug, stop              | diagnostics, output, test results                 |
| Realtime application backend     | commands and queries          | domain notifications and live updates             |

These are architectural patterns, not bundled transports or domain-specific
implementations. `pyrpckit` supplies the typed protocol layer between them.

## How it fits together

The programming model has four small pieces:

1. A `RpcRouter` groups methods and server-initiated notifications by namespace.
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

Pydantic is installed automatically. Client generation additionally needs the
optional `codegen` extra:

```bash
uv add "pyrpckit[codegen]"
# or: python -m pip install "pyrpckit[codegen]"
```

No web framework or transport dependency is included.

## A bidirectional gateway in one protocol

An agent run makes the two directions concrete. Commands enter the gateway while
typed updates leave it:

```python
from typing import Literal

from pyrpckit import RpcApp, RpcModel, RpcRouter


class StartRunParams(RpcModel):
    prompt: str


class RunRef(RpcModel):
    run_id: str


class SteerRunParams(RpcModel):
    run_id: str
    instruction: str


class TextDelta(RpcModel):
    type: Literal["text.delta"] = "text.delta"
    run_id: str
    delta: str


class ToolCall(RpcModel):
    type: Literal["tool.call"] = "tool.call"
    run_id: str
    call_id: str
    name: str
    arguments: dict[str, object]


class ToolResult(RpcModel):
    type: Literal["tool.result"] = "tool.result"
    run_id: str
    call_id: str
    output: str


type AgentUpdate = TextDelta | ToolCall | ToolResult
agent = RpcRouter(namespace="agent", tags=("agent",))


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


@agent.notification("run.update")
def run_update() -> AgentUpdate:
    """Updates emitted while an agent run is active."""


app = RpcApp(version=1)
app.include_router(agent)
server = app.bind(AgentMethods(service))
```

The protocol now contains `agent.run.start`, `agent.run.steer`, and the
`agent.run.update` notification. A generated TypeScript client makes all of them
discoverable:

```typescript
const run = await client.agent.run.start({
  prompt: "Investigate the deployment failure",
});

// This can be triggered while the notification stream is still active.
await client.agent.run.steer({
  runId: run.runId,
  instruction: "Check the logs first",
});

for await (const notification of client.agent.run.update()) {
  switch (notification.type) {
    case "text.delta":
      renderText(notification.delta);
      break;
    case "tool.call":
      showPendingTool(notification.name, notification.arguments);
      break;
    case "tool.result":
      showToolResult(notification.callId, notification.output);
      break;
  }
}
```

The notification `type` literals become a discriminated union in generated clients.
`RpcModel` gives request, result, and notification payloads one explicit
protocol-level base class and applies the camel-case and strict-field wire
conventions at their definition. Generated clients derive their corresponding
types from the contract, so there is no second set of handwritten socket payload
types to keep in sync.

## Declaring routes

```python
from pyrpckit import RpcApp, RpcError, RpcModel, RpcRouter


class GetAutomationParams(RpcModel):
    automation_id: str


class AutomationResponse(RpcModel):
    id: str
    name: str


class AutomationNotFound(RpcError):
    code = -32004
    message = "Automation not found"


router = RpcRouter(namespace="automation", tags=("automation",))


class AutomationRpcMethods:
    def __init__(self, service: AutomationService) -> None:
        self._service = service

    @router.method(errors=(AutomationNotFound,))
    async def get(self, params: GetAutomationParams) -> AutomationResponse:
        """Get an automation."""
        job = await self._service.get(params.automation_id)
        return AutomationResponse(id=job.id, name=job.name)
```

The namespace supplies the logical JSON-RPC and generated-client hierarchy once,
while tags are documentation metadata. Handler classes need no base class. The
bare `@router.method` form uses the Python function name, so this method is
exposed as `automation.get` and generated beneath `client.automation`.

`RpcModel` is the canonical base for request, response, and notification payloads.
It makes the protocol boundary explicit in the type hierarchy: Python fields use
`snake_case`, OpenRPC and JSON use `camelCase`, explicit Pydantic aliases win,
and unknown input fields are rejected.

For a small one-off method, keyword-only arguments remain available as a compact
alternative. The router derives an internal params model from them, and any
supported return annotation becomes the result schema:

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
utility_router = RpcRouter()


@utility_router.method
async def ping() -> None:
    pass
```

## Composing the app

Include routers once to create the complete API definition:

```python
app = RpcApp(version=1)
app.include_router(router)
app.include_router(utility_router)
```

An include takes a snapshot. An optional include namespace is prepended to the
router namespace with a dot, and include tags are appended with ordered
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

The same router can be mounted under multiple namespaces. One instance normally
serves every mount; bind mounts explicitly when they need different state:

```python
mounted_app = RpcApp()
primary = mounted_app.include_router(router, namespace="primary")
secondary = mounted_app.include_router(router, namespace="secondary")

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
def to_rpc_error(error: Exception) -> RpcError | None:
    if isinstance(error, HttpxTimeout):
        return RpcError("Upstream timed out", code=-32005)
    return None


server = app.bind(AutomationRpcMethods(service), error_mapper=to_rpc_error)
```

Anything neither declared nor mapped becomes an internal error, so handler
internals never leak to clients.

## Server-initiated notifications

Notifications declare their payload through the return annotation of a normal
function. Members of a payload union pin a `type` field to a literal, so clients
can narrow the union; a single payload model needs no discriminator. The models
need no decorator, and unions are validated when the app protocol is frozen. On
the JSON-RPC wire, a notification has no `id`:

```python
class AutomationStarted(RpcModel):
    type: Literal["automation.started"] = "automation.started"
    automation_id: str


type AutomationUpdate = AutomationStarted | AutomationFinished

notifications = RpcRouter(
    namespace="automation",
    tags=("automation",),
    server="production",
)


@notifications.notification("update")
def automation_update() -> AutomationUpdate:
    """Publish an automation lifecycle update."""


app.include_router(notifications)
```

Send one with the `RpcNotification` envelope.

## Generating the contract

The contract is a build-time artefact, so no running server is involved. For a
deployment-aware contract, pair the app with typed OpenRPC server metadata:

```python
from pyrpckit import OpenRpcContract, OpenRpcServer, ServerVariable


CONTRACT = OpenRpcContract(
    app=app,
    title="Automation",
    servers=(
        OpenRpcServer(
            name="production",
            url="wss://{host}/automation/rpc",
            variables={
                "host": ServerVariable(default="api.example.com"),
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

`RpcRouter.server` references an `OpenRpcServer.name`; it does not name a
transport implementation. Every method and notification on that router is
associated with the referenced server in the OpenRPC document. Contract creation
fails with an actionable error when the server is missing or declared more than
once. This keeps API hierarchy, deployment endpoint, and transport metadata
separate:

```python
control = RpcRouter(namespace="browser.control", server="control")
screencast = RpcRouter(namespace="browser.screencast", server="screencast")
```

The generated endpoint helpers already preserve server URLs and variables.
Generated clients currently remain transport-agnostic and accept one transport;
the route-to-server metadata is retained so an explicit multi-endpoint client
runtime can be added without changing the contract format.

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
pyrpckit generate schema/greeting.openrpc.json \
  --language python \
  --output src/greeting_client \
  --package greeting_client \
  --client-name GreetingClient
```

TypeScript clients use the same OpenRPC input and language-neutral IR:

```bash
pyrpckit generate schema/greeting.openrpc.json \
  --language typescript \
  --output src/generated \
  --client-name GreetingClient \
  --transport-module ../transport
```

This writes a small root client, domain-oriented files under `namespaces/`, models,
route metadata, declared errors, and a private client core. The transport module
stays outside the generated directory and exports this transport-agnostic
contract:

```typescript
export interface RpcTransport {
  request<TResult>(method: string, params?: object): Promise<TResult>;
  notifications(): AsyncIterable<unknown>;
  close(): Promise<void>;
}
```

The generated Python package holds no hand-written code and is meant to be committed:

- `models.py` — reachable Pydantic models and type aliases
- `namespaces/<group>.py` — the route hierarchy as small domain classes
- `routes.py` — exact wire names and response and notification adapters
- `endpoints.py` — server URL templates when the contract declares servers
- `errors.py` — stably named declared remote errors
- `client.py` — the root facade and transport lifecycle
- `__init__.py` — a small curated public surface
- `.rpcgen/manifest.json` — generated-file ownership and contract digest

```python
async with GreetingClient(transport) as client:
    greeting = await client.greeting.say(name="Mathis")  # -> SayResult
    async for notification in client.greeting.changed():  # -> GreetingUpdate
        print(notification)
```

Only the schemas the client actually reaches are emitted — request and response
envelopes stay out of the generated models. Python clients embed their transport
protocol and accept any structurally compatible implementation. TypeScript clients
import the equivalent `RpcTransport` interface from `--transport-module`. Both
therefore work over a WebSocket, HTTP, or a queue.

Run the generator with `--check` in CI to fail the build when the committed
client no longer matches the server schema:

```bash
pyrpckit generate schema/greeting.openrpc.json \
  --language python \
  --output src/greeting_client \
  --check
```

Use `--api-root browser --api-name nav=navigation` to shorten an explicit common
wire namespace and choose domain names without changing any JSON-RPC method. For
several contracts, put the same settings in `rpc-clients.toml` and run
`pyrpckit generate --config rpc-clients.toml`; `--check` verifies the whole
batch without writing.

## Development

Small, direct library examples live in [`examples/`](examples/).

This project uses [uv](https://docs.astral.sh/uv/) for dependency management.

```bash
uv sync --all-groups
uv run pre-commit install
uv run ruff check .
uv run ruff format .
uv run pytest
```
