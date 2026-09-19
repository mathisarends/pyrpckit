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
  TypeScript clients generated from
  [`automation.py`](generated_clients/automation.py). That module declares
  server methods, an event, binary streams, and client methods, and exports the
  contract that becomes
  [`automation.openrpc.json`](generated_clients/automation.openrpc.json). The
  client methods `tasks.approve` and `browser.dialogs.confirm` generate the
  handler classes in
  [`client_methods.py`](generated_clients/python/automation_client/client_methods.py).
  The binary streams generate
  [`streams.py`](generated_clients/python/automation_client/streams.py) and
  [`streams.ts`](generated_clients/typescript/streams.ts). Regenerate the
  contract and both clients from this directory's
  [`rpcgen.toml`](generated_clients/rpcgen.toml):

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
