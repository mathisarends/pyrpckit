# FastAPI example

This example is deliberately a calculator, not an application. Its files show the
complete path from a decorated Python API to a typed generated client without
hiding the transport boundary.

## 1. Declare the API

[`api.py`](api.py) is the source of truth. Pydantic models define params and
results, `@rpc.method` exposes handler methods, and the feature groups them into a
protocol. The implementation remains plain Python and has no FastAPI dependency.

```python
class CalculatorRpc:
    @rpc.method("calculator.add")
    async def add(self, params: BinaryOperationParams) -> CalculationResult:
        return CalculationResult(value=params.left + params.right)
```

## 2. Attach a transport

[`app.py`](app.py) contains the whole FastAPI integration: one POST route passes
decoded JSON to `RpcServer`, then serializes its response. The GET route publishes
the generated OpenRPC contract.

Run it from the repository root:

```bash
uv run --group example python -m scripts.serve_fastapi_example
```

Then call the protocol directly:

```bash
curl http://127.0.0.1:8000/rpc \
  -H "content-type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"calculator.add","params":{"left":20,"right":22}}'
```

## 3. Generate the client

The committed [`calculator.openrpc.json`](calculator.openrpc.json) and the
[`examples/calculator_client`](../calculator_client/) package are generated
artifacts. The client lives beside the FastAPI app because it represents a
separate consumer. Update both after changing the API:

```bash
uv run --group example python -m scripts.generate_fastapi_example
```

The convenience script renders OpenRPC with `render_openrpc`, then calls
`generate_python_client`. Both are public APIs; the equivalent standalone client
command is `pyrpckit generate python ...`.

The useful part of the generated surface is intentionally small:

```python
async with CalculatorClient(transport) as client:
    result = await client.calculator.divide(left=84, right=2)
    print(result.value)  # 42.0
```

[`transport.py`](transport.py) is the only hand-written client integration. It
implements the small `RpcTransport` protocol with HTTPX; generated code is not
tied to FastAPI or HTTP.

With the server running, execute the complete client example:

```bash
uv run --group example python -m scripts.call_fastapi_example
```

Run the generation script with `--check` in CI to catch a committed contract or
client that no longer matches the decorated API.
