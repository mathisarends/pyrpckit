# pyrpckit implementation bridge

Date: 2026-09-08
Branch: `main`
Last implementation commit: `a7bb083 Regenerate structured example clients`

## Current state

`SPEC.md` and `CLIENT_GEN_SPEC.md` are implemented through the final client
regeneration step. The project now has one pre-v1 composition API:
`RpcRouter` declares routes, `RpcApp` composes them, and `app.bind(...)` creates
an `RpcServer`. The old `RpcHandler`, global `@method`, `feature(...)`,
`notification(...)`, public `RpcProtocol`, and direct `RpcServer(...)`
construction were removed rather than deprecated.

OpenRPC contracts use the PascalCase schema names and camelCase wire fields that
were requested. Public package exports in `pyrpckit/__init__.py` use relative
imports, and this convention is recorded in `AGENTS.md`.

Both client generators consume only OpenRPC and share the same hierarchical IR.
Python and TypeScript now produce matching logical API trees. For example:

```text
Python                              TypeScript
api/__init__.py                     api/index.ts
api/tasks/__init__.py               api/tasks/index.ts
api/tasks/status.py                 api/tasks/status.ts
```

The TypeScript runtime module is named `core.ts`; it is internal because the
root `index.ts` does not export it. Generated packages also contain models,
metadata, declared errors, optional endpoints, and an ownership manifest.
Jinja2 templates provide the stable module wrappers while the emitters build
the language-specific declarations.

Generated Python methods use direct keyword arguments. Generated TypeScript
methods use typed parameter objects. Both clients expose typed `events()` and
idempotent lifecycle handling. TypeScript wire properties remain camelCase, as
shown by `taskId` in the example.

## Commits made during this implementation

- `508bccd Model hierarchical generated clients`
- `b05ad73 Generate idiomatic Python client packages`
- `6655eff Generate structured TypeScript clients`
- `853c042 Track generated package ownership`
- `f4e9b25 Name declared errors in OpenRPC contracts`
- `5dcc6eb Generate client batches from TOML`
- `4bb66ec Remove legacy codegen layout concepts`
- `39282c1 Document structured generated clients`
- `11a517b Use an idiomatic TypeScript core module`
- `658ecf1 Remove the pre-v1 composition API`
- `7cc66bf Avoid unused endpoint metadata imports`
- `bea298b Match generated TypeScript to Prettier`
- `a7bb083 Regenerate structured example clients`

## Validation completed

- `uv run pytest -q`: 205 passed.
- `uv run ruff check .`: passed.
- `uv run ruff format --check .`: passed before final regeneration; generated
  Python files also passed the pre-commit Ruff hooks.
- `npx.cmd prettier --check examples/typescript_client/generated
  examples/typescript_client/usage.ts examples/typescript_client/transport.ts`:
  passed after the final formatting run.
- `uv run --group showcase python -m scripts.fastapi_showcase.generate --check`:
  passed.
- A strict TypeScript 7.0.2 compile passed earlier with `strict`,
  `exactOptionalPropertyTypes`, `noUncheckedIndexedAccess`, and
  `verbatimModuleSyntax`. The final `core.ts` rename only changed module paths;
  rerunning this check is the first recommended follow-up.
- `uv build` passed earlier and the wheel contained all Jinja2 templates.

## Generated examples now committed

- `examples/typescript_client/generated/` uses `core.ts` and separate
  `api/tasks/index.ts` and `api/tasks/status.ts` files.
- `showcase/client/` uses `api/calculator.py`; the old `namespaces/` package is
  deleted.
- Both generated trees contain `.pyrpckit-generated.json` so future runs can
  safely remove only obsolete generated files.

## Recommended next steps

1. Run a final strict TypeScript compile once a local `tsc` executable is
   available without an `npx` download:

   ```powershell
   npx.cmd --yes --package typescript tsc --project <strict-tsconfig.json>
   ```

   Include `examples/typescript_client/**/*.ts` and keep the strict options
   listed above.

2. Run `uv build` once more after any follow-up changes and inspect the wheel to
   ensure `pyrpckit/codegen/templates/**/*.j2` remain packaged.

3. If more generator behavior is added, keep Python and TypeScript API path
   tests paired in `tests/codegen/test_layout.py` and regenerate committed
   clients only after implementation and tests are complete.

4. A possible later enhancement is mapping route-specific declared error codes
   to generated subclasses at runtime. Error classes and metadata are already
   generated; generic `RpcRemoteError` behavior is complete.

The repository should be clean after committing this bridge file. No client
regeneration remains pending.
