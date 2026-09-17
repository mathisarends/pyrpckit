# Examples

These examples show the pyrpckit API directly, without a web framework, generated
client, or application structure. Each file is standalone and executable.

- [`basic.py`](basic.py) declares a typed method, serves one raw JSON-RPC request,
  and prints the response.
- [`features.py`](features.py) mounts multiple channels on one versioned
  service.
- [`notifications.py`](notifications.py) decorates an injected async event
  source and declares its typed payload.
- [`schemas.py`](schemas.py) renders JSON Schema and OpenRPC documents from the
  same protocol definition.
- [`generated_clients`](generated_clients) contains inspectable Python and
  TypeScript clients generated from the same OpenRPC document. Its
  `x-rpckit-binary-streams` entry (`browser.screencast.frames`) generates a
  stream at `client.browser.screencast.frames()`, plus
  [`streams.py`](generated_clients/python/automation_client/streams.py) and
  [`streams.ts`](generated_clients/typescript/streams.ts), the binary
  WebSocket helpers alongside the regular JSON-RPC client. Regenerate both
  examples from this directory's [`rpcgen.toml`](generated_clients/rpcgen.toml):

  ```bash
  uv run python -m pyrpckit.codegen.cli generate \
    --config examples/generated_clients/rpcgen.toml
  ```

Run an example from the repository root:

```bash
uv run python examples/basic.py
uv run python examples/features.py
uv run python examples/notifications.py
uv run python examples/schemas.py
```
