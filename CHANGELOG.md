# Changelog

## 0.6.0 - 2026-09-15

### Added

- Describe raw binary WebSocket media channels with the
  `x-rpckit-binary-streams` OpenRPC extension and the public `BinaryStream`
  contract type.
- Generate typed binary-stream endpoint helpers and transport interfaces for
  Python and TypeScript. Bundled WebSocket clients exchange native binary
  frames, enforce stream direction, and keep media traffic separate from the
  JSON-RPC control connection.

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
