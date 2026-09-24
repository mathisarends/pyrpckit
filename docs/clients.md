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
    base_url="https://{host}",
    variables={
        "host": ServerVariable(
            default="api.example.com",
            description="API host",
        )
    },
)
```

Endpoint paths, names, subprotocols, protocol version, typed errors, and binary
streams are derived from the service. `base_url` accepts either an HTTP or
WebSocket URL (or a URL template); `http` and `https` are translated to `ws`
and `wss`. Every supplied variable must occur in the resulting server URLs.

Use `contract.write("tasks.openrpc.json")` to write canonical UTF-8 JSON, or
`contract.to_json()` to get the same text. `contract.to_openrpc()` returns the
complete document as a dictionary for integrations that need one.

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
extra_files = ["src/extra/authenticated.py"]
extra_exports = ["connect_gateway"]

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
hand. `extra_files` copies Python source files into the generated package by
filename; `extra_exports` adds uniquely defined names from those files to the
package root and manifest. This keeps custom connection helpers across
regeneration.

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

For refreshed credentials, pass an async header factory. It runs for each
WebSocket connection, including binary stream connections:

```python
async def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {await tokens.current()}"}


async with TasksClient.connect(headers=auth_headers) as client:
    ...
```

An HTTP or HTTPS `url=` override is converted to WS or WSS for the RPC socket.

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
server for each call. `connect()` opens every declared server in parallel, so
authentication and network failures occur at connection time. Pass `lazy=True`
or `lazy: true` to open each socket when a method routed to it is first called;
concurrent first calls share the same connection attempt:

```python
async with TasksClient.connect(host="stage.example.com", lazy=True) as client:
    ...
```

```ts
const client = await TasksClient.connect({
  host: "stage.example.com",
  lazy: true,
});
```

Python clients expose `client.closed`, which resolves with the cause of the
first unexpected WebSocket disconnect, or `None` after a normal close. Pass
`reconnect=True` to retry failed connections with bounded backoff and resume
active subscriptions on the new socket. The delay can be adjusted with
`reconnect_initial_delay` and `reconnect_max_delay`.

```python
async with TasksClient.connect(reconnect=True) as client:
    cause = await client.closed
    if cause is not None:
        print(f"Connection lost: {cause}")
```

TypeScript clients accept `onDisconnect` to observe unexpected socket loss.
The callback receives the socket error; callers can create a new client when
needed.

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

Contracts with [client methods](client-methods.md) also generate abstract Python
handler classes that `connect(handlers=...)` registers.

The exact client class, method arguments, endpoint names, and server variables
come from the document. See
[`examples/generated_clients`](../examples/generated_clients) for complete
generated Python and TypeScript packages.

[Back to documentation](README.md)
