# Generated TypeScript client example

Run the generator from the repository root:

```bash
uv run python examples/typescript_codegen.py
```

The Python example declares the task protocol, renders its OpenRPC document in
memory, and regenerates the files in `generated/`:

- `generated/models.ts` contains the reachable wire types.
- `generated/api/` contains the typed route hierarchy as `Api` classes.
- `generated/metadata.ts` keeps exact wire names and route metadata.
- `generated/core.ts` contains the transport runtime shared by the API files.
- `generated/client.ts` contains the root client and event stream.
- `generated/index.ts` exposes the small public generated API.
- `transport.ts` is the small hand-written contract implemented by an HTTP,
  WebSocket, or other transport.
- `usage.ts` shows what calling the generated API looks like.

Everything in `generated/` is generator output and can be replaced at any time.
The private manifest lets later runs remove obsolete generated files without
touching hand-written files.
