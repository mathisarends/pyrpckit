# Generated TypeScript client example

Run the generator from the repository root:

```bash
uv run python examples/typescript_codegen.py
```

The Python example declares the task protocol, renders its OpenRPC document in
memory, and regenerates the files in `generated/`:

- `generated/models.ts` contains wire types and enum values.
- `generated/client.ts` contains the typed namespace facade.
- `generated/index.ts` exposes the public generated API.
- `transport.ts` is the small hand-written contract implemented by an HTTP,
  WebSocket, or other transport.
- `usage.ts` shows what calling the generated API looks like.

Everything in `generated/` is generator output and can be replaced at any time.
