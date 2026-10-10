# Middleware and OpenTelemetry

Instrument RPC calls, notifications, connections, and binary streams with
`middleware=`. Lifecycle hooks run around execution, so tracing context reaches
application handlers and their child tasks. Existing `observer=` callbacks
continue to work alongside middleware.

## Enable OpenTelemetry

```bash
uv add "pyrpckit[fastapi,otel]"
```

The extra installs the OpenTelemetry API. Configure the SDK and exporters in
your application, or pass providers from your existing telemetry setup. rpckit
does not create exporters, replace global providers, or shut down application
providers. Without a configured SDK, the OTEL API uses its normal no-op behavior.
[OpenTelemetry Python instrumentation](https://opentelemetry.io/docs/languages/python/instrumentation/)

```python
from collections.abc import AsyncIterator

from fastapi import FastAPI

from rpckit import RpcChannel, RpcService
from rpckit.fastapi import create_router
from rpckit.opentelemetry import OpenTelemetry

media = RpcChannel("media")


@media.stream(content_type="application/octet-stream")
async def frames() -> AsyncIterator[bytes]:
    yield b"frame"


rpc = RpcService(middleware=[OpenTelemetry()])
rpc.stream("/media/frames", frames)

web = FastAPI()
web.include_router(create_router(rpc))
```

Configure the integration at one location. For endpoints mounted through
FastAPI dependencies, it can live on `RpcRoutes` instead:

```python
routes = RpcRoutes(
    router,
    context=open_session,
    middleware=[OpenTelemetry()],
)
routes.mount(session_media)
```

Here the router, context function, and stream endpoint belong to the application.
`middleware=` is also accepted by `create_router()`, `serve_websocket()`,
endpoint `serve()`, service `socket()` and `stream()`, and channel/endpoint
`create_server()`.

## Configure signals and providers

```python
telemetry = OpenTelemetry(
    tracing=True,
    metrics=True,
    connection_spans=False,
    record_exceptions=False,
    tracer_provider=None,
    meter_provider=None,
    exclude=None,
)
```

Provider arguments accept the OpenTelemetry API provider interfaces. Omitted
providers use the global providers. `tracing` and `metrics` operate independently.
Each RPC call, subscription control call, or incoming notification gets a
`SERVER` span. Each accepted binary stream gets an `INTERNAL` span covering
dependency resolution, execution, and cleanup. Application spans started in
handlers inherit the active operation span.

An existing valid trace context, including FastAPI's connection context, takes
precedence. Otherwise the configured OTEL propagator extracts context from
case-insensitive handshake headers. `connection_spans=True` adds a connection
span; the default avoids adding another connection span to FastAPI telemetry.

`exclude` receives `RpcConnectionScope` or `RpcRequestScope`. Return `True` to
skip that operation. Excluding a connection also excludes its operations and
frame metrics. Parallel connections have independent exclusion state.

Payloads, header values, concrete path variables, query strings, request IDs,
and close reasons are not captured automatically. Span names and metric labels
use registered operations, endpoint names, and path templates. Unknown methods
receive the span name `RPC` without a raw method attribute. Error categories
and status remain available with exception events disabled; explicitly enable
`record_exceptions` to include exception messages and stack traces.

## Metrics

These are rpckit-specific instrument names. Durations are measured in seconds.

| Instrument | Measurement |
| --- | --- |
| `rpckit.rpc.calls` | Completed calls, including error and cancellation outcomes |
| `rpckit.rpc.active` | Calls executing now |
| `rpckit.rpc.duration` | Call duration, excluding the concurrency queue and socket delivery |
| `rpckit.connections` | Accepted connections and rejected or failed handshakes |
| `rpckit.connections.active` | Accepted connections currently open |
| `rpckit.connections.duration` | Accepted connection lifetime |
| `rpckit.streams` | Completed binary stream operations by close category |
| `rpckit.streams.active` | Binary streams executing now |
| `rpckit.streams.duration` | Binary stream duration |
| `rpckit.stream.frames` | Binary messages, split by `inbound` and `outbound` |
| `rpckit.stream.bytes` | Binary bytes in each direction |
| `rpckit.notifications` | Outgoing notification count by registered operation |
| `rpckit.slow_consumers` | JSON-RPC slow-consumer closures |

Frame counters update while a stream is running and work independently of trace
sampling. They count binary application messages, including empty messages.
The input-end control message is excluded. Output counts follow successful
socket sends; input counts follow validation and enqueueing. Invalid or rejected
input and failed sends are excluded. Generator output and `RpcBinaryOutput`
writes share the same counting rules.

No span or retained trace event is created for each frame. Stream spans are
exported when the operation ends; use live metrics to observe long streams.
Normal disconnects and shutdown cancellations do not mark spans as errors.

## Write middleware

Subclass `RpcMiddleware` and override the scopes or existing observer callbacks
you need. `RpcMiddlewareLike` supports structural implementations of all three
lifecycle methods. The base class provides no-op implementations.

```python
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from rpckit import RpcMiddleware, RpcStreamScope

logger = logging.getLogger(__name__)


class StreamTiming(RpcMiddleware):
    @asynccontextmanager
    async def stream(self, scope: RpcStreamScope) -> AsyncIterator[None]:
        try:
            yield
        finally:
            outcome = scope.outcome
            if outcome is not None:
                logger.info(
                    "stream=%s duration=%s close=%s",
                    scope.endpoint.stream.name,
                    outcome.duration,
                    outcome.close_code,
                )
```

The three hooks are `connection(RpcConnectionScope)`,
`request(RpcRequestScope)`, and `stream(RpcStreamScope)`. Each returns an async
context manager. Scope endpoint metadata is exposed through the read-only
`RpcEndpointInfo` and `RpcStreamEndpointInfo` protocols. Requests also expose
their `RpcRequestContext` and registered method definition, where available.
Connection scopes begin with handshake metadata and gain their `connection`
during preparation. Stream scopes have an accepted connection on entry.

Before cleanup, the runtime assigns an immutable `scope.outcome` with the
duration, response or close information, original error, and cancellation
status. Stream outcomes retain errors caught in reader and writer tasks, even
after mapping them to close codes. Middleware enter and exit run in the same
task; batch items and concurrent requests use separate scopes.

Middleware composes from outermost to innermost: serving adapter, service,
endpoint. Each sequence enters in list order and exits in reverse order.
Configuration sequences are copied; operation state belongs in scopes or local
context-manager variables. Instrumentation failures are logged and isolated,
and middleware cannot suppress application failures or rewrite responses.
Cancellation propagates after cleanup.

`RpcRoutes` opens connection middleware before its `context=` dependency and
closes it after dependency cleanup, including mapped context rejections.
Earlier FastAPI or router-wide dependencies remain outside that boundary.
Manual routes start their boundary when they call `serve_websocket()`.

Custom `RpcRoutes.mount(handler=...)` handlers also report acceptance, close
codes, and connection lifetime when they use FastAPI's `WebSocket` directly.
RPC and stream scopes begin when a custom handler delegates to
`serve_websocket()`. Additional `middleware=` supplied there runs inside the
connection scopes already opened by the route, and participates in request,
stream, and activity callbacks. Instances already active on that connection
are reused without entering their scopes twice. Middleware added during
delegation finishes when delegated serving ends; route middleware also covers
the surrounding dependency cleanup.

Generated-client instrumentation, browser trace propagation, and OTEL log
exporting are separate from this server-side integration.

[Back to documentation](README.md)
