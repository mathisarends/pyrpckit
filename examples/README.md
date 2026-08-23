# Examples

These examples show the pyrpckit API directly, without a web framework, generated
client, or application structure. Each file is standalone and executable.

- [`basic.py`](basic.py) declares a typed method, serves one raw JSON-RPC request,
  and prints the response.
- [`no_params.py`](no_params.py) declares methods that take no params and answer
  with nothing, and shows how they reach the OpenRPC contract.
- [`features.py`](features.py) groups multiple handlers into named features and
  inspects the resulting versioned protocol.
- [`errors.py`](errors.py) declares an application error as part of a method's
  contract and returns it as a JSON-RPC failure.
- [`error_mapping.py`](error_mapping.py) translates an exception from foreign code
  with an `error_mapper`.
- [`notifications.py`](notifications.py) declares a typed union of events and
  serializes a server-initiated notification.
- [`schemas.py`](schemas.py) renders JSON Schema and OpenRPC documents from the
  same protocol definition.
- [`typescript_codegen.py`](typescript_codegen.py) renders OpenRPC and generates
  the inspectable client under [`typescript_client/generated`](typescript_client/generated),
  with example calls in [`typescript_client/usage.ts`](typescript_client/usage.ts).

Run an example from the repository root:

```bash
uv run python examples/basic.py
uv run python examples/no_params.py
uv run python examples/features.py
uv run python examples/errors.py
uv run python examples/error_mapping.py
uv run python examples/notifications.py
uv run python examples/schemas.py
uv run python examples/typescript_codegen.py
```

For a complete FastAPI integration and generated client, see the
[`showcase/`](../showcase/README.md).
