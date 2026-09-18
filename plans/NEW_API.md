# pyrpckit 0.6 API

This is the implementation checklist for the 0.6 breaking release. Source is
authoritative for details not repeated here. There is no compatibility layer
for the pre-0.6 composition API.

## Public model

- `RpcChannel(name=None, /, *, namespace=None, raises=(),
  resolver_scope=call_scope)` groups methods, events, and streams. A supplied
  name provides the default namespace; otherwise the namespace is also the
  channel identity. `namespace=""` creates root operations when a name exists.
- `channel.child(segment, raises=(), resolver_scope=None)` creates a nested
  namespace that inherits the parent's errors and scope. Mounting the root
  includes all descendants.
- `RpcService(*, version=1, error_mapper=None, on_request=None,
  on_response=None, limits=None)` owns the complete application and its serving
  defaults.
  Examples call the instance `app`, matching common FastAPI usage.
- Mount JSON-RPC with `app.socket(path, *, channels, name=None,
  error_mapper=None, limits=None, subprotocol=None, summary=None)`.
- Mount a binary stream with `app.stream(path, decorated_stream, name=None,
  subprotocol=None, summary=None)`.
- A channel may be mounted only once; a stream may be mounted only once.
  Endpoint names, endpoint path shapes, and fully qualified RPC names must be
  unique.
- `freeze()` materializes immutable protocol metadata and prevents later
  registration. `protocol` delegates to it.
- Remove `RpcModule`, channel inclusion/modules, `RpcApp`, `RpcRouter`,
  `RpcAPIRouter`, and `RpcContract.from_channels()`.
- Remove authoring-side tags entirely. `RpcChannel`, `.method()`, `.event()`,
  `.stream()`, protocol definitions, and service contract generation do not
  accept or emit tags. Codegen may still read tags from external OpenRPC input.

## Definitions

### Methods

```python
@channel.method(name=None, summary=None, raises=())
async def operation(...) -> Result: ...
```

- Decorate async free functions only.
- Wire name is `<namespace>.<explicit-or-function-name>`.
- A request is either one Pydantic model parameter or direct named parameters;
  `Inject[T]` parameters are server-only.
- Channel and method `raises` are merged and deduplicated.
- `errors=` is removed in favor of `raises=`.

### Events

```python
@channel.event(name=None, payload=EventModel, summary=None)
async def events(...) -> AsyncIterator[EventModel]: ...
```

- Event sources are async iterators started once per connection.
- Each item is validated and sent as a JSON-RPC notification.
- Event failure closes the connection; cancellation and cleanup are owned by
  the runtime.

### Binary streams

```python
@channel.stream(name=None, content_type="application/octet-stream", summary=None)
async def frames(...) -> AsyncIterator[bytes]: ...

app.stream("/sessions/{session_id}/frames", frames)
```

- 0.6 streams are server-to-client only and run on a separate binary socket.
- Yielded values must be bytes. JSON encoding and RPC dispatch are bypassed.
- Client-to-server and bidirectional input documents are rejected by codegen.

## Connections and serving

- `RpcSocket` is the transport protocol: handshake metadata, `accept`,
  `reject`, `receive`, text/binary send, and `close`.
- `RpcHandshake`, `RpcConnection`, `RpcConnectionClose`, `RpcDisconnect`,
  `RpcRejection`, and `RpcLimits` are public.
- `app.serve(socket, ..., root_path="")` matches the handshake path and serves
  the selected endpoint. `RpcEndpoint.serve()` and
  `RpcStreamEndpoint.serve()` serve an already selected endpoint.
- JSON-RPC requests are concurrent and bounded by `RpcLimits`; responses may
  complete out of order. Notifications receive no response.
- Async `on_request` and `on_response` hooks expose structured request data,
  response outcome, and elapsed time for instrumentation.
- `RpcConnection.close_code` and `.close_reason` expose the final local or peer
  close information to connection-scoped resources.
- The runtime owns socket acceptance and closure. A missing route is rejected,
  malformed framing closes with a protocol error, and disconnect is normal.
- The library logger is `logging.getLogger(LOGGER_NAME)`, with public
  `LOGGER_NAME = "pyrpckit"` in `pyrpckit.constants`.

- Authentication belongs to the hosting framework and runs before `serve()`.
  The runtime does not provide connect hooks or an authentication model.
- `RpcConnection` remains injectable in handlers for connection metadata and
  controlled closure.

## Errors

Application errors subclass `RpcError`:

```python
class ProjectMissing(RpcError):
    code = "project_missing"
    rpc_code = -32004
    message = "Project not found"
    details: ProjectMissingDetails
```

- `code` is a stable snake-case application code. It defaults from the class
  name.
- `rpc_code` is the numeric JSON-RPC code and defaults to `-32000`.
- `message` defaults from `code` and can be overridden per instance.
- Optional `details` is declared by annotating one Pydantic model type.
  Construction accepts either that model or its fields.
- The wire error uses numeric `error.code`; `error.data` contains string
  `code` and optional serialized `details`.
- Built-ins cover parse error, invalid request, method not found, invalid
  params, and internal error with structured details where applicable.
- Unexpected exceptions pass through an optional `RpcErrorMapper`, otherwise
  become internal errors and are logged without leaking details.

## Dependency resolution

- Retain `Inject`, `RpcResolver`, `RpcResolverScope`, and `call_scope`.
- Direct `context` may be one object or a type-to-object mapping and is layered
  over an optional resolver.
- Calls use the declared resolver scope. Connect-hook results live for the
  socket. Cleanup follows the owning async context.

## Contracts and schema

- `app.contract(title=..., base_url=..., description=..., variables=...)`
  returns `RpcContract` from the mounted service.
- Every JSON-RPC endpoint becomes an OpenRPC server with WebSocket transport
  metadata. Endpoint path variables become server variables.
- Every binary endpoint becomes an `x-rpckit-binary-streams` entry with
  `direction: "server-to-client"`, URL, content type, binary frame type,
  variables, and optional subprotocol.
- Protocol version is emitted as `x-rpckit-version`.
- Declared errors use `x-rpckit-code` and optional
  `x-rpckit-details-schema`; remove `x-rpckit-name` and
  `x-rpckit-data-schema`.
- Schema export accepts `RpcContract` as the strict source for deployed
  contracts. OpenRPC generation contains no implicit tags.

## Client generation

Generators continue to consume only the OpenRPC document.

### Typed errors

- IR errors contain numeric `rpc_code`, string `code`, message, and optional
  details type.
- Generate one concrete exception/error class per declared application code.
- Python validates details through Pydantic before constructing the concrete
  error. TypeScript exposes typed details.
- Transports dispatch received failures through the generated registry.
  Unknown or invalid error payloads fall back to `RpcRemoteError` without
  hiding the original response.
- Generated base errors expose numeric RPC code, string application code,
  message, and details. Also generate `RpcConnectionClosed`.

### Streams

- Rename generated `media.py` / `media.ts` to `streams.py` / `streams.ts`.
- Add stream operations to the same root/namespace tree as RPC methods.
- Generated stream connections are receive-only, async iterable, closable,
  and usable as async context managers (or async-disposable in TypeScript).
- Python stream methods return an awaitable/context-manager opening object.
  TypeScript methods return `Promise<BinaryStreamConnection>`.
- A custom client may receive a stream opener. Bundled WebSocket `connect()`
  configures `BinaryWebSocketStream.open` automatically.
- Stream URL variables and overrides are resolved by the generated operation.
- Generated code must pass Ruff (Python) or Prettier (TypeScript).

## Framework and testing adapters

- `pyrpckit.fastapi.create_router(app, ...)` creates routes for every service
  endpoint. `FastApiSocket` adapts FastAPI/Starlette WebSockets. Remove the old
  `serve()` helper and redundant module-level `__all__` in this leaf module.
- FastAPI rejection uses an HTTP denial response when supported, otherwise the
  defined WebSocket close code.
- `RpcTestClient(app, path, ...)` provides requests, notifications, binary
  receive, connection lifecycle, context, resolver, mapper, and limits without
  a network server.

## Public package and repository updates

- Re-export the public 0.6 types from `pyrpckit.__init__` with relative imports.
- Set package version to `0.6.0` only in `pyproject.toml`; `__version__` matches
  the installed release API.
- Update README, changelog, examples, generated fixtures, and `AGENTS.md` to the
  service/channel model. Use lowercase `app` for service instances.
- Remove obsolete source modules and tests for the old app/router/module API.
- No stale repository references to `RpcModule`, `from_channels`, method
  `errors=`, `x-rpckit-name`, `x-rpckit-data-schema`, old FastAPI `serve`, or
  generated `media.py` / `media.ts`, except migration notes.
- Verification: Ruff check/format, full pytest suite, generated-client runtime
  tests, contract round trips, and generator formatting checks.
