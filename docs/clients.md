# Contract and clients

The service definition is the source of truth. pyrpckit exports it as OpenRPC,
then generates clients from that document rather than from Python internals.
The resulting artifact can therefore be reviewed, versioned, and consumed by
other tooling.

Install the generator extra:

```bash
uv add "pyrpckit[codegen]"
```

## Build a contract

```python
from pyrpckit import ServerVariable

contract = app.contract(
    title="Tasks API",
    description="Realtime task operations.",
    base_url="wss://{host}",
    variables={
        "host": ServerVariable(
            default="api.example.com",
            description="API host",
        )
    },
)
```

Endpoint paths, names, subprotocols, protocol version, typed errors, and binary
streams are derived from the service. `base_url` must be a WebSocket URL or a
URL template. Every supplied variable must occur in the resulting server URLs.

## One configuration for contract and clients

Put the importable contract in a module, then create `rpcgen.toml` beside your
project paths:

```toml
version = 1

[contract]
source = "my_api:contract"
output = "schema/tasks.openrpc.json"

[[clients]]
language = "python"
output = "src/tasks_client"
package = "tasks_client"
with_transport = "websocket"

[[clients]]
language = "typescript"
output = "frontend/src/tasks-client"
with_transport = "websocket"
```

Generate everything together:

```bash
pyrpckit generate --config rpcgen.toml
```

Paths are resolved relative to the config file. Client generation reads the
rendered OpenRPC document; it does not receive the live `RpcService` or
`RpcProtocol` object.

Run the same command in CI with `--check`. It exits nonzero and lists stale
files instead of writing them:

```bash
pyrpckit generate --config rpcgen.toml --check
```

Generated directories are owned by the generator and should not be edited by
hand.

## Generate from an existing document

The schema and client stages can also run separately:

```bash
pyrpckit schema my_api:contract \
  --output schema/tasks.openrpc.json

pyrpckit generate schema/tasks.openrpc.json \
  --language python \
  --output src/tasks_client \
  --package tasks_client \
  --with-transport websocket
```

Omit `with_transport` when integrating with an existing transport. Python
clients accept an `RpcTransport`; TypeScript clients accept the corresponding
transport interface. The OpenRPC generators are tolerant of ordinary tagged
OpenRPC methods and additionally understand pyrpckit's typed-error and binary
stream extensions.

## Use a generated client

The generated package root exports the client, models, namespace classes,
declared error classes, endpoint metadata, routes, and transport building
blocks. Applications normally need only the client and their domain models.

With the generated WebSocket transport, Python exposes an async connection
context:

```python
from tasks_client import TasksClient

async with TasksClient.connect(host="api.example.com") as client:
    task = await client.tasks.create(title="Write docs")
    async for update in client.tasks.updated():
        print(update)
```

Pass handshake headers directly when authentication is transport-owned:

```python
async with TasksClient.connect(
    host="api.example.com",
    headers={"Authorization": f"Bearer {token}"},
) as client:
    ...
```

When the client outlives a context manager, await the connection directly. The
caller then owns the returned client and must close it:

```python
client = await TasksClient.connect(host="api.example.com")
try:
    ...
finally:
    await client.close()
```

TypeScript connects explicitly and should be closed when no longer needed:

```ts
const client = await TasksClient.connect({ host: "api.example.com" });
try {
  const task = await client.tasks.create({ title: "Write docs" });
} finally {
  await client.close();
}
```

TypeScript projects with explicit resource management can express the same
lifecycle with `await using`:

```ts
await using client = await TasksClient.connect({ host: "api.example.com" });
const task = await client.tasks.create({ title: "Write docs" });
```

Closing a generated client is idempotent and closes every transport the client
owns.

## Lazy connections and multiple servers

Contracts may route different methods to different WebSocket servers. A
generated client still presents one namespace tree and selects the declared
server for each call. A single-server client opens eagerly so authentication or
network failures occur at connection time. Multi-server clients open each
socket only when a method routed to that server is first called; concurrent
first calls share the same connection attempt.

Pass `eager=True` / `eager: true` to open every server in parallel, or false to
force lazy behavior:

```python
async with TasksClient.connect(host="stage.example.com", eager=True) as client:
    ...
```

```ts
const client = await TasksClient.connect({
  host: "stage.example.com",
  eager: true,
});
```

Top-level server variables such as `host` apply to every server and binary
stream that declares that variable. Override only exceptional deployments with
the generated server names:

```python
from tasks_client import ServerName, TasksClient

async with TasksClient.connect(
    host="stage.example.com",
    servers={
        ServerName.MEDIA: "wss://media.stage.example.com/rpc",
    },
) as client:
    ...
```

```ts
const client = await TasksClient.connect({
  host: "stage.example.com",
  servers: { media: "wss://media.stage.example.com/rpc" },
});
```

An endpoint override may also be a generated `Endpoint` object when its URL and
subprotocols need to be supplied together. Unknown server names and values
outside a variable's declared enum are rejected instead of being silently
accepted.

The generated `endpoints.<server>()` factories are the typed escape hatch for
that advanced case. Each factory exposes only the variables declared by its
server and preserves the server's default subprotocols:

```python
from tasks_client import ServerName, TasksClient, endpoints

async with TasksClient.connect(
    servers={
        ServerName.MEDIA: endpoints.media(host="media.stage.example.com"),
    },
) as client:
    ...
```

```ts
import { endpoints, TasksClient } from "./tasks-client";

const client = await TasksClient.connect({
  servers: {
    media: endpoints.media({ host: "media.stage.example.com" }),
  },
});
```

The map key and the endpoint returned by the factory must name the same server.

## Custom transports and hooks

Use `with_transports()` in Python or `withTransports()` in TypeScript for tests
and custom adapters. Pass either one transport for a single-server contract or
a transport map keyed by the generated server names. Set `close_transport=False`
or `closeTransport: false` when another component owns those transports.

Client hooks can observe or wrap every request through the `hooks` option on
`connect()` and the custom-transport constructor. Socket factories are also
injectable, which keeps the generated runtime independent of a particular
WebSocket package and makes connection behavior testable without a network.

The exact client class, method arguments, endpoint names, and server variables
come from the document. See
[`examples/generated_clients`](../examples/generated_clients) for complete
generated Python and TypeScript packages.

[Back to documentation](README.md)
