# Changelog

## 0.5.0 - 2026-09-10

### Added

- Export every generated TypeScript model from the package root, so request and
  response types no longer require a manually configured `./models` subpath.
- Accept typed `context` values in `RpcChannel.server()` for lightweight tests,
  scripts, and custom transports without a bespoke resolver.
