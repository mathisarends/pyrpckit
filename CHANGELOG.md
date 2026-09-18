# Changelog

## 0.6.0 - Unreleased

### Fixed

- Prevent Starlette's normal WebSocket teardown cancellation from escaping
  FastAPI handlers as `concurrent.futures.CancelledError`.

### Changed

- Keep authentication in the hosting framework before `serve()` instead of
  coupling it to the RPC service lifecycle. `RpcConnection` remains injectable
  in handlers for connection metadata and controlled closure.
- Configure default error mapping and runtime limits on `RpcService`, with
  optional endpoint overrides, so production routes and `RpcTestClient` share
  the same serving behavior.
- Pass mounted channels through the explicit `channels=` sequence argument.
- Add nested channels with `channel.child()`. Children inherit their parent's
  namespace, declared errors, and resolver scope, and are included when the root
  channel is mounted. Channel names may default to a dotted `namespace=`.
- Explain dotted operation-name errors with the nested-channel solution.
- Derive application error codes by stripping either `Error` or `RpcError`,
  allow `RpcInvalidParamsError(message=...)` without synthetic validation
  issues, and suggest `message=` for accidental positional strings.
- Add async `on_request` and `on_response` server hooks with structured request,
  outcome, and duration context for logging and instrumentation.
- Expose `close_code` and `close_reason` on `RpcConnection`, including close
  information received from the peer.
- Resolve FastAPI dependencies per connection with `resolver_factory=`. Add
  `dishka_router()` to read Dishka's APP container from `app.state` and reject
  accidentally supplied SESSION containers with a targeted error.

### Removed

- Remove server-side connect hooks, `ConnectionRejected`, and the
  authentication-specific `RpcRejection.UNAUTHORIZED` and `FORBIDDEN` values.

## 0.5.0 - 2026-09-18

### Added

- Add `RpcService`, socket endpoints, connection hooks, transport-independent
  serving, and an in-memory `RpcTestClient`.
- Add receive-only binary stream definitions and endpoints, represented by the
  `x-rpckit-binary-streams` OpenRPC extension.
- Add stable string error codes and typed error details alongside numeric
  JSON-RPC codes.
- Generate concrete typed errors and namespaced stream clients for Python and
  TypeScript. Bundled WebSocket clients open stream sockets automatically.
- Export generated models, namespace classes, errors, routes, transport types,
  and stream types from each client package root.
- Add request hooks to both generated clients and async disposal support to the
  TypeScript client.
- Add `create_router()` and `FastApiSocket` as the FastAPI integration.
- Accept typed `context` values in `RpcChannel.server()` for lightweight tests,
  scripts, and custom transports without a bespoke resolver.
- Add an optional `[contract]` source and output to codegen TOML configs. A
  single `pyrpckit generate --config ...` invocation now renders the OpenRPC
  contract and every client, and `--check` verifies that complete pipeline.
- Let generated TypeScript WebSocket transports and endpoint overrides accept
  `URL` objects. Endpoint `subprotocols` are optional and inherit their contract
  default; single-server clients also accept `connect({ url })`.

### Changed

- Configure FastAPI prefixes and dependencies through `include_router()` so
  `create_router()` only exposes pyrpckit runtime options.
- Rename generated `RpcRemoteError.code` from the numeric JSON-RPC code to the
  stable string application code; the numeric value is now `rpc_code` in Python
  and `rpcCode` in TypeScript.
- Include `data.code` and `data.details` in every error envelope, including
  built-in errors whose details are `null`.
- Process requests concurrently up to `RpcLimits.max_concurrency`; responses
  may complete out of order even when requests arrive on the same connection.
- Keep FastAPI WebSocket handler signatures free of captured endpoint
  parameters and preserve unexpected WebSocket state errors.
- Make `RpcChannel` a lightweight group of methods, events, and streams; the
  channel name is positional and supplies the default namespace.
- Generate contracts from mounted services, including endpoint paths,
  variables, subprotocols, protocol version, errors, and streams.
- Rename generated `media.py` / `media.ts` to `streams.py` / `streams.ts`.
- Rename method `errors=` to `raises=` and error extensions to
  `x-rpckit-code` / `x-rpckit-details-schema`.
- Redesign generated `connect()` around one option set, shared server variables,
  per-server overrides, injectable socket factories, and lazy sockets. Pass
  `eager=True` / `eager: true` to open every declared server in parallel.
- Place binary stream operations on their declared namespace and let their URL
  templates inherit variables supplied to `connect()`.
- Let optional Python request parameters remain unset so server-side schema
  defaults still apply.
- Treat regular binary stream closure as normal async-iteration completion and
  report it as `RpcStreamClosed` only for direct reads.
- Preserve notification pump completion so subscriptions created after a
  transport ends finish or fail immediately instead of waiting forever.

### Removed

- Remove the old app/router/module composition API, channel inclusion, and
  `RpcContract.from_channels()`.
- Remove authoring-side tags and the old FastAPI `serve()` helper.
- Remove client-to-server and bidirectional binary streams.
- Replace generated `from_transport*` / `fromTransport*` constructors with
  `with_transports()` / `withTransports()`.
- Stop exporting the generated TypeScript `ConnectOptions` and
  `<Name>Transports` types; the options are declared inline on `connect()` and
  `withTransports()`.
- Remove awaiting a Python `BinaryStreamOpening` directly; use `async with` or
  call `.open()` and close the returned connection explicitly.
