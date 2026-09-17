# Changelog

## 0.6.0 - 2026-09-17

### Added

- Add `RpcService`, socket endpoints, connection hooks, transport-independent
  serving, and an in-memory `RpcTestClient`.
- Add receive-only binary stream definitions and endpoints, represented by the
  `x-rpckit-binary-streams` OpenRPC extension.
- Add stable string error codes and typed error details alongside numeric
  JSON-RPC codes.
- Generate concrete typed errors and namespaced stream clients for Python and
  TypeScript. Bundled WebSocket clients open stream sockets automatically.
- Add `create_router()` and `FastApiSocket` as the FastAPI integration.

### Changed

- Make `RpcChannel` a lightweight group of methods, events, and streams; the
  channel name is positional and supplies the default namespace.
- Generate contracts from mounted services, including endpoint paths,
  variables, subprotocols, protocol version, errors, and streams.
- Rename generated `media.py` / `media.ts` to `streams.py` / `streams.ts`.
- Rename method `errors=` to `raises=` and error extensions to
  `x-rpckit-code` / `x-rpckit-details-schema`.

### Removed

- Remove the old app/router/module composition API, channel inclusion, and
  `RpcContract.from_channels()`.
- Remove authoring-side tags and the old FastAPI `serve()` helper.
- Remove client-to-server and bidirectional binary streams.

## 0.5.0 - 2026-09-10

### Added

- Export every generated TypeScript model from the package root, so request and
  response types no longer require a manually configured `./models` subpath.
- Accept typed `context` values in `RpcChannel.server()` for lightweight tests,
  scripts, and custom transports without a bespoke resolver.
- Add an optional `[contract]` source and output to codegen TOML configs. A
  single `pyrpckit generate --config ...` invocation now renders the OpenRPC
  contract and every client, and `--check` verifies that complete pipeline.
- Let generated TypeScript WebSocket transports and endpoint overrides accept
  `URL` objects. Endpoint `subprotocols` are optional and inherit their contract
  default; single-server clients also accept `connect({ url })`.
- Support declarative `modules=` composition in `RpcChannel` and add an explicit,
  idempotent `freeze()` method. Freeze errors now identify the channel and
  explain that contract generation may have materialized its protocol.
