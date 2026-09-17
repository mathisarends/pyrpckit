# Binary streams

Binary streams carry server-to-client bytes on a dedicated socket. They are a
good fit for audio, video frames, screenshots, or other data that should not be
base64-encoded inside JSON-RPC messages.

## Define and mount a stream

```python
from collections.abc import AsyncIterator

from pyrpckit import Inject, RpcChannel, RpcService


class FrameSource:
    async def frames(self) -> AsyncIterator[bytes]:
        yield b"frame"


media = RpcChannel("media")


@media.stream(content_type="image/jpeg", summary="Live preview frames.")
async def preview(source: Inject[FrameSource]) -> AsyncIterator[bytes]:
    async for frame in source.frames():
        yield frame


app = RpcService()
app.stream("/projects/{project_id}/preview", preview)
```

The function must be an async generator annotated as `AsyncIterator[bytes]` or
`AsyncGenerator[bytes, ...]`. It may accept injected dependencies, but no
client-supplied parameters. Each decorated stream can be mounted once.

The stream's operation name is `media.preview`; the mounted path becomes its
own WebSocket endpoint. Path variables are filled from the contract's server
variables by generated clients.

## Lifecycle

The connect hook and dependency lifecycle work exactly as they do for a
JSON-RPC endpoint. The socket closes normally when the generator finishes. A
disconnect cancels the generator, and an exception closes the stream as an
internal error.

Streams are deliberately receive-only. Use a regular RPC method to send
configuration or control commands, then open the stream to consume bytes.

## Contract and generated clients

OpenRPC has no standard binary-stream primitive, so pyrpckit writes stream
metadata to `x-rpckit-binary-streams`. Both generators consume that extension
and expose stream operations beside the regular namespace APIs. With the
generated WebSocket transport, the client also receives a ready-to-use binary
stream opener.

## Test a stream

```python
from pyrpckit.testing import RpcTestClient


async with RpcTestClient(
    app,
    "/projects/demo/preview",
    context={FrameSource: FrameSource()},
) as client:
    assert await client.next_frame() == b"frame"
```

`request()` is unavailable on stream endpoints, and `next_frame()` is
unavailable on JSON-RPC endpoints.

[Back to documentation](README.md)
