# Examples

These examples show the pyrpckit API directly, without a web framework, generated
client, or application structure.

- [`basic.py`](basic.py) declares a typed method, serves one raw JSON-RPC request,
  and prints the response.

Run an example from the repository root:

```bash
uv run python examples/basic.py
```

For a complete FastAPI integration and generated client, see the
[`showcase/`](../showcase/fastapi_app/README.md).
