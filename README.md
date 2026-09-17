# pyrpckit

**Define your realtime API once in Python. Get the server, the contract, and
typed clients for Python and TypeScript — none of which can drift apart.**

Agents, browser automation, live dashboards and voice need more than
request/response over HTTP: server-pushed events, binary streams, one
long-lived connection. So the JSON-RPC envelope gets hand-written, the dispatch
table grows by hand, and the frontend client is maintained separately — until
the two disagree in production.

pyrpckit makes the Python definition the single source of truth:

| You write | pyrpckit gives you |
| --- | --- |
| an async function on a channel | validated dispatch, injection, concurrency, shutdown |
| a payload model | an OpenRPC contract as a build-time artifact |
| an error class | typed exceptions in every generated client |
| an async iterator | server-pushed events and binary streams |
| nothing else | Python and TypeScript clients, regenerated in CI |

The core has no HTTP or WebSocket dependency — a FastAPI adapter ships with it,
and any transport you already have can serve a pyrpckit service.

## The idea

You define each operation once, on the server:

```python
@tasks.method()
async def create(params: CreateTask, store: Inject[TaskStore]) -> Task:
    return await store.create(params.title)


@tasks.event(payload=TaskUpdated)
async def updated(store: Inject[TaskStore]) -> AsyncIterator[TaskUpdated]:
    async for task in store.watch():
        yield TaskUpdated(task=task)
```

One command turns that into an OpenRPC document and clients in both languages:

```bash
pyrpckit generate --config rpcgen.toml
```

And your frontend gets the whole API fully typed — no schema written twice, no
client kept in sync by hand, no stringly-typed method names:

```ts
const task = await client.tasks.create({ title: "Ship 0.6" }); // Task

for await (const update of client.tasks.updated()) {           // TaskUpdated
  render(update.task);
}
```

Run `--check` in CI and a definition that outgrew its clients fails the build
instead of shipping.

## Install

```bash
uv add pyrpckit
uv add "pyrpckit[fastapi]"  # FastAPI adapter
uv add "pyrpckit[codegen]"  # client generation
```

Python 3.12 or newer. Pydantic is the only required dependency.

## Quickstart

Channels group related operations and provide their namespace; a service mounts
them on a socket. Nothing here needs a running server to test:

```python
from pyrpckit import Inject, RpcChannel, RpcModel, RpcService
from pyrpckit.testing import RpcTestClient


class CreateTask(RpcModel):
    title: str


class Task(RpcModel):
    id: int
    title: str


class TaskStore:
    def __init__(self) -> None:
        self._tasks: list[Task] = []

    async def create(self, title: str) -> Task:
        task = Task(id=len(self._tasks) + 1, title=title)
        self._tasks.append(task)
        return task


tasks = RpcChannel("tasks")


@tasks.method()
async def create(params: CreateTask, store: Inject[TaskStore]) -> Task:
    """Create a task."""
    return await store.create(params.title)


app = RpcService(version=1)
app.socket("/rpc", tasks)


async def test_create() -> None:
    async with RpcTestClient(app, "/rpc", context={TaskStore: TaskStore()}) as client:
        assert await client.request("tasks.create", {"title": "Ship 0.6"}) == {
            "id": 1,
            "title": "Ship 0.6",
        }
```

`tasks.create` is the wire name, the docstring becomes the contract summary,
and `Inject[TaskStore]` is resolved on the server — it never appears in the
public schema.

## Documentation

- [Services and channels](docs/services.md) — methods, namespaces, parameter
  styles, sockets, protocol versions
- [Dependency injection](docs/dependencies.md) — `Inject[T]`, resolvers,
  scopes, Dishka
- [Connections and events](docs/connections-and-events.md) — the `connect`
  hook, rejecting handshakes, server-pushed events, limits
- [Typed errors](docs/errors.md) — stable codes, typed details, generated
  exception classes
- [Binary streams](docs/streams.md) — receive-only byte streams beside JSON-RPC
- [Contract and clients](docs/clients.md) — `rpcgen.toml`, the CLI, the shape
  of generated clients
- [Transports](docs/transports.md) — FastAPI, custom sockets, testing

## Examples

[`examples/`](examples) holds standalone runnable scripts, and
[`examples/generated_clients`](examples/generated_clients) contains real
generated Python and TypeScript output you can read before installing
anything.

## Development

```bash
uv sync --all-groups
uv run ruff check .
uv run ruff format .
uv run pytest
```
