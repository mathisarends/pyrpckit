# Changelog

## 0.7.0 - Unreleased

### Added

- Add callbacks, requests the server sends to a connected client.
  `channel.callback(name, params=..., result=..., raises=...)` declares a typed
  `RpcCallback`. Callback names share the name space of methods, events, and
  streams.
- Make `RpcPeer` injectable on socket endpoints. `peer.call(callback, params,
  timeout=...)` validates params and results, raises declared errors as their
  typed exceptions, and raises `RpcCallbackRemoteError`,
  `RpcCallbackResultError`, `RpcCallbackTimeoutError`, or
  `RpcPeerClosedError` otherwise. Server-originated requests use `server:<n>`
  string IDs, and outgoing calls respect `RpcLimits`.
- Describe callbacks under the `x-rpc-callbacks` OpenRPC extension, in the same
  shape as `methods`.
- Generate one abstract callback class per namespace in Python clients, plus
  `CallbackHandler` and `callback_dispatcher()`. `connect(callbacks=...)`
  registers handlers, which run concurrently. Declared errors of callbacks gain
  `create()`.
- Accept `callbacks=` in `RpcTestClient`.

### Changed

- Generated Python WebSocket transports answer incoming requests. Requests
  without a registered handler get `-32601 Method not found`, where they used to
  fail the transport.
- `RpcTestClient` reads the socket in the background.
  `next_notification()` now raises `RpcTestConnectionClosed` after a disconnect.
- Raise the generated client layout version to 10.

## 0.6.0 - Unreleased

### Added

- Add client-to-server and bidirectional binary streams. The handler signature
  determines the direction: async functions inject `RpcBinaryInput` and/or
  `RpcBinaryOutput`, and async generators remain server-to-client. The client
  ends its input with the text message `{"type":"end"}`. A disconnect before the
  end aborts the handler. Incoming frames use a bounded queue, which applies
  backpressure. `RpcInputEndMessage` and the generated `BinaryInputEnd` model
  describe the end message.
- Treat stream handler parameters without `Inject[...]` as typed path
  variables. They are validated before the handshake is accepted and rejected
  as `NOT_FOUND`.
- Add `input_content_type=` to `@channel.stream()`. Write `direction` and
  `inputContentType` to `x-rpckit-binary-streams`.
- Generate `BinarySender` and `BinaryChannel` stream clients with `send()`,
  `end_input()` / `endInput()`, and `end()` in Python and TypeScript. `end()`
  gives up after 30 seconds by default (`TimeoutError` in Python,
  `RpcStreamTimeout` in TypeScript). Error closes now raise `RpcStreamFailed`, and policy violations
  (1008) raise `RpcStreamRefused`.
- Add `send_frame()`, `end_input()`, and `closed()` to `RpcTestClient`.

### Fixed

- Prevent Starlette's normal WebSocket teardown cancellation from escaping
  FastAPI handlers as `concurrent.futures.CancelledError`.
- Keep generated Python clients `ruff check` and `ruff format` clean for
  contracts without events or without methods, servers without variables, and
  single-letter model names.
- Omit the `./models` export from generated TypeScript clients whose contract
  declares no models, so they compile.
- Ignore leading underscores when deriving error codes, so private error
  classes such as `_MissingError` produce `missing`.

### Changed

- Make generated route metadata carry its request and response types. Python
  now serializes generated Pydantic parameter models centrally in the client
  runtime, while TypeScript infers request results and notification payloads
  from the selected route instead of repeating manually supplied generics.
- Rename the generated `BinaryStreamConnection` to `BinaryReceiver`.
- Open generated Python streams only with `async with`. Stream methods now
  return an async context manager; `BinaryStreamOpening` and its `open()`
  method are gone, so an opened stream can no longer be left unclosed.
- Generated stream methods only accept the stream's own path variables, such
  as a session ID. Variables that a server also declares, such as `host`, come
  from `connect()`. The per-call `url` override is gone; to redirect a stream,
  wrap the `stream_opener` or `stream_socket_factory`. In turn, `connect()`
  only accepts server variables, not the path variables of a single stream.

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
- Add a service-bound `observer=` for request start, request finish, and
  connection close events with structured outcome and duration contexts.
- Expose `close_code` and `close_reason` on `RpcConnection`, including close
  information received from the peer.
- Let generated Python WebSocket clients accept `headers=`, make their
  connection object directly awaitable, and eagerly open single-server clients
  by default. Multi-server clients remain lazy unless requested otherwise.
- Accept HTTP base URLs when building contracts and translate them to WebSocket
  schemes. Add `RpcContract.to_openrpc()` and render committed JSON with Unicode
  characters intact.
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
- Derive an endpoint's default server name from the final static path segment
  (for example, `/v1/gateway` becomes `gateway`).
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
