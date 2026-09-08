# FastAPI showcase

This showcase is deliberately a calculator, not an application. Its files show the
complete path from a decorated Python API to a typed generated client without
hiding the transport boundary.

## 1. Declare the API

[`api/`](api/) is the source of truth. Pydantic models, declared errors, handlers,
and router/app composition each have a small focused module. Its `__init__.py`
exposes the complete public contract. The implementation remains plain Python and
has no FastAPI dependency.

```python
router = rpc.RpcRouter(prefix="calculator", tags=("calculator",))


class CalculatorRpc:
    @router.method("add")
    async def add(self, params: BinaryOperationParams) -> CalculationResult:
        return CalculationResult(value=params.left + params.right)
```

## 2. Attach a transport

[`server.py`](server.py) contains the whole FastAPI integration: one POST route
passes decoded JSON to `RpcServer`, then serializes its response. The contract is
not served at runtime — it is rendered from the protocol with `pyrpckit schema`
and committed.

Run it from the repository root:

```bash
uv run --group showcase python -m scripts.fastapi_showcase.serve
```

Then call the protocol directly:

```bash
curl http://127.0.0.1:8000/rpc \
  -H "content-type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"calculator.add","params":{"left":20,"right":22}}'
```

## 3. Generate the client

The committed [`calculator.openrpc.json`](../spec/calculator.openrpc.json) in
`showcase/spec` and the modules in [`showcase/client`](../client/) are generated
artifacts. The client lives beside the FastAPI app because it represents a
separate consumer. Update both after changing the API:

```bash
uv run --group showcase python -m scripts.fastapi_showcase.generate
```

The convenience script renders the contract with `render_contract`, then calls
`generate_python_client`. Both are public APIs; the equivalent standalone
commands are `pyrpckit schema ...` and `pyrpckit generate python ...`.

The useful part of the generated surface is intentionally small:

```python
async with CalculatorClient(transport) as client:
    result = await client.calculator.divide(left=84, right=2)
    print(result.value)  # 42.0
```

[`client/transport.py`](../client/transport.py) is the only hand-written client
integration. It implements the small `RpcTransport` protocol with HTTPX; generated
code is not tied to FastAPI or HTTP.

With the server running, execute the complete client call:

```bash
uv run --group showcase python -m scripts.fastapi_showcase.call
```

Run the generation script with `--check` in CI to catch a committed contract or
client that no longer matches the decorated API.
