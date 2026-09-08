# Implemented

## Adapt annotated models to the RPC wire contract

Let `@router.method(...)` derive the RPC wire behavior from the annotated parameter
and result models, so users do not have to inherit every contract type from
`rpc.RpcModel`:

```python
class SearchParams(BaseModel):
    project_id: str
    query: str
    max_results: int = 10


class SearchResult(BaseModel):
    items: list[str]
    next_page_token: str | None = None


class SearchRpc:
    @router.method
    async def run(self, params: SearchParams) -> SearchResult:
        return SearchResult(items=[])
```

- Inspect the method annotations when the protocol is assembled and create internal
  request/result adapters with the standard RPC wire conventions.
- Apply the default `snake_case` to `camelCase` mapping consistently during input
  validation, output serialization, OpenRPC generation, and client generation.
- Do not mutate or monkey-patch the user's model classes globally; adaptation must be
  local to the RPC contract.
- Preserve explicit aliases and model validation behavior.
- Keep `rpc.RpcModel` available as an optional explicit convenience base, but do not
  require it for RPC methods.
- Detect alias collisions and unsupported model types with clear protocol definition
  errors.

## Support signature-based RPC parameters and results

Additionally, consider allowing small handlers to declare their parameters directly:

```python
@router.method
async def run(
    self,
    *,
    query: str,
    limit: int = 10,
) -> list[str]:
    return []
```

This is separate from automatic model adaptation and should not replace explicit
request/result models for reusable or complex contracts.

## Make method names optional

Use the decorated function name as the RPC method name by default. Together with a
router prefix, the concise declaration below exposes `search.run`:

```python
router = rpc.RpcRouter(prefix="search")


class SearchRpc:
    @router.method
    async def run(self, params: SearchParams) -> SearchResult:
        return SearchResult(items=[])
```

Keep the callable decorator form for configuration without requiring an explicit
method name:

```python
@router.method(errors=(SearchUnavailable,))
async def run(self, params: SearchParams) -> SearchResult: ...
```

Also retain an explicit method-name override for a deliberately stable wire name or
when the Python and RPC names need to differ:

```python
@router.method("run", errors=(SearchUnavailable,))
async def execute_search(self, params: SearchParams) -> SearchResult: ...
```

- Support all three forms: `@router.method`, `@router.method(...)`, and
  `@router.method("wire_name", ...)`.
- Infer the name before applying the router prefix and run the inferred name through
  the same validation and duplicate detection as an explicit name.

### Example migration note

Update README examples, doc examples, and tests that demonstrate the normal API to
use the elegant inferred-name form (`@router.method`). Show the explicit-name form
only in a dedicated example that explains intentional wire-name overrides and API
stability; do not present it as required boilerplate.
