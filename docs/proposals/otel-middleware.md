# Middleware and OpenTelemetry API proposal

Status: draft for discussion. The APIs below are proposed additions and are
not implemented. This proposal targets the serving runtime, including the
existing FastAPI integration and dedicated binary streams.

The recommendation is to add transport-independent lifecycle middleware and
ship an optional `rpckit.opentelemetry.OpenTelemetry` implementation. Existing
observers remain supported. Applications can use the same public hooks to
implement their own instrumentation.

## Existing hooks and the missing lifecycle

[`RpcObserver`](../../rpckit/observer.py) already receives request start and
finish, accepted connection start and finish, binary frame sizes, notification
activity, and slow-consumer events. It can be configured on a service or an
endpoint. Callback failures are logged without replacing application results.

There are four gaps in the current implementation:

- A request cancelled in `RpcServer._handle_one()` skips `request_finished()`.
- Connection callbacks start after `_prepare()` accepts the socket, so they
  do not report rejected handshakes.
- Stream failures are mapped to close codes inside the runtime. The closing
  callback receives the close information without the original exception.
- Paired callbacks do not provide a context-manager boundary for activating
  and resetting tracing context around dependencies and handler execution.

The FastAPI documentation now describes built-in connection telemetry and
provider configuration. We should borrow its small configuration surface and
reuse application providers. Its WebSocket telemetry describes a connection;
rpckit can additionally describe RPC calls and binary stream operations.
[FastAPI OpenTelemetry documentation](https://fastapi.tiangolo.com/advanced/opentelemetry/)

The existing `fastapi>=0.115` extra does not guarantee those newer FastAPI
telemetry APIs are available. rpckit instrumentation should work with a
configured OpenTelemetry SDK independently of FastAPI's telemetry setup.

## Recommended application API

Install the FastAPI adapter and the proposed telemetry extra:

```console
uv add "pyrpckit[fastapi,otel]"
```

Configure instrumentation once on the service:

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

For applications that configure integrations where they mount routes, expose
the same `middleware=` keyword on `RpcRoutes`, `create_router`, and
`serve_websocket`:

```python
from fastapi import APIRouter

from rpckit.fastapi import RpcRoutes
from rpckit.opentelemetry import OpenTelemetry

router = APIRouter(prefix="/jobs")
routes = RpcRoutes(
    router,
    context=open_job,
    middleware=[OpenTelemetry()],
)
routes.mount(job_output)
routes.mount(job_events)
```

Here `open_job`, `job_output`, and `job_events` are the application's existing
context function and endpoints. This is an alternative registration location
to the service example.

Expose `middleware=` on `RpcService.socket()`, `RpcService.stream()`, endpoint
`serve()`, and channel/endpoint `create_server()` as well. Use
`Sequence[RpcMiddlewareLike] = ()` consistently. Standalone servers execute
request middleware without requiring a connection.

Registration composes in this outer-to-inner order: serving adapter, service,
endpoint. Within each sequence, the first entry is outermost; cleanup runs in
reverse order. Endpoint middleware is additional to service middleware. A
`create_server()` call inherits the endpoint chain where applicable.

The assembled chain enters each scope once even when `RpcRoutes` delegates to
`serve_websocket` and then `endpoint.serve()`. Configuration is copied into
tuples; connection-specific state belongs to a scope, not the shared middleware
instance. Configure an OTEL instance at one registration location.

## Public lifecycle hooks

Add `RpcMiddleware` and `RpcMiddlewareLike` to the root import surface.
`RpcMiddleware` extends the existing no-op `RpcObserver` with these methods:

```python
from contextlib import AbstractAsyncContextManager


class RpcMiddleware(RpcObserver):
    def connection(
        self, scope: RpcConnectionScope
    ) -> AbstractAsyncContextManager[None]: ...

    def request(self, scope: RpcRequestScope) -> AbstractAsyncContextManager[None]: ...

    def stream(self, scope: RpcStreamScope) -> AbstractAsyncContextManager[None]: ...
```

These signatures are an API sketch. The base implementation supplies no-op
context managers, so an extension only overrides the scopes it needs.
`RpcMiddlewareLike` structurally describes the three lifecycle methods;
activity callbacks remain optional, as with existing observers.

| Scope | Information on entry | Final outcome before cleanup |
| --- | --- | --- |
| `RpcConnectionScope` | Endpoint definition and `RpcHandshake`; `connection` is initially optional and becomes available during preparation | Accepted or rejected, rejection category, close category and raw code, duration, original error, cancellation |
| `RpcRequestScope` | Existing `RpcRequestContext`, endpoint if present, registered method definition when identified | Response or notification completion, duration, original error, cancellation |
| `RpcStreamScope` | Accepted connection, endpoint and stream definition, including direction and content types | Completion, close category and raw code, duration, original error, cancellation |

Publish these scope types and their corresponding `RpcConnectionOutcome`,
`RpcRequestOutcome`, and `RpcStreamOutcome` types. A scope is owned by one
operation. Its `outcome` is initially `None`; the runtime assigns an immutable
outcome before unwinding the middleware. It reflects the mapped wire result as
well as the original exception, including errors caught inside child tasks.

For example, custom instrumentation can wrap the stream operation:

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from rpckit import RpcMiddleware, RpcStreamScope


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

`logger` is the application's logger. An OTEL implementation uses the same
boundary to activate a span, so application code can create child spans with
the standard OpenTelemetry API.

Keep the existing `stream_frame_sent(connection, size)` and
`stream_frame_received(connection, size)` callbacks available on middleware.
They report application binary messages, not underlying WebSocket fragments.
Output is counted after a successful socket send; input is counted after
validation and successful enqueueing. The input-end control message is not a
binary frame. Generator output and `RpcBinaryOutput.send()` use the same hooks.

The existing `observer=` API retains its registration and callback semantics.
Middleware activity is dispatched alongside the legacy observer. There is no
required migration and no replacement of `before_accept`, DI, or error mapping.

## Runtime guarantees

Connection scopes begin before core handshake checks and `before_accept`.
Stream scopes start after acceptance and include dependency resolution,
generator iteration or coroutine execution, task cleanup, and generator
closure. Request scopes include validation, dependency resolution, execution,
result validation, and error mapping; they end before transport delivery.

Batch items receive independent request scopes. Malformed JSON and rejected
empty or oversized batches receive one request scope without a registered
method. Failed notifications receive an outcome even though no response is
sent. A request duration excludes waiting for the concurrency semaphore.

Every successfully entered scope exits once on success, rejection, disconnect,
handler failure, or cancellation. Cancellation is preserved after cleanup.
Normal disconnects and shutdown cancellations are classified separately from
application failures. Record why child tasks were cancelled: cancellation
because a sibling failed must retain the failure in the operation outcome.

Run middleware enter and exit in the same asyncio task. Connection context
must be active before spawning reader, writer, event, or stream tasks; request
context must be activated inside the individual request task. Concurrent calls
and batch siblings must not inherit one another's active spans. Outcome
collection and cleanup must also handle failure during context teardown.

Instrumentation failures are logged and isolated, following the observer's
existing behavior. A failed enter does not prevent serving; a failed exit does
not replace the response or cancellation. Middleware cannot suppress an
application exception or rewrite a protocol result. This contract is for
instrumentation; authorization remains in the existing hooks and dependencies.

For `RpcRoutes`, enter the assembled connection chain in the generated route's
first dependency, before `context=` resolves, and finalize it after dependency
cleanup. This covers context errors and mapped rejections. FastAPI dependencies
that run earlier, such as router-wide dependencies, remain outside this scope.
With a manually written route, `serve_websocket()` starts its scope when called.

## Official OpenTelemetry extension

Propose this constructor surface:

```python
OpenTelemetry(
    tracing=True,
    metrics=True,
    connection_spans=False,
    record_exceptions=False,
    tracer_provider=None,
    meter_provider=None,
    exclude=None,
)
```

`exclude` receives a `RpcConnectionScope` or `RpcRequestScope` and returns
whether that operation should be skipped. Excluding a connection excludes its
children. Excluding a request leaves the connection and other requests intact.
`tracing` and `metrics` can be enabled independently. Provider objects are
optional; the extension otherwise uses the global OTEL API providers.

The `otel` extra adds `opentelemetry-api`. SDK setup, resources, exporters,
sampling, flushing, and shutdown remain application responsibilities. This
follows OpenTelemetry's guidance for instrumented libraries.
[OpenTelemetry Python instrumentation](https://opentelemetry.io/docs/languages/python/instrumentation/)

| Signal | Proposed default behavior |
| --- | --- |
| RPC tracing | One `SERVER` span per call or notification, named from a registered method |
| Stream tracing | One `INTERNAL` operation span per binary stream, named from its declared operation |
| Connection tracing | Disabled by default; opt in for applications that need an additional connection span |
| Request metrics | Completed calls, active calls, duration, and failure classification |
| Connection metrics | Active accepted connections, acceptance and rejection counts, close classification, accepted lifetime |
| Stream metrics | Active streams, duration, binary message counts and byte totals in each direction |

Frame counters update while the stream is running, independently of trace
sampling. The first version creates no span or retained event per binary frame.
Long streams therefore remain observable before their operation span ends.
Notification callbacks can feed notification counts and byte totals; because
the existing callback lacks a connection argument, per-endpoint attribution
needs a separately discussed callback addition.

Reuse a valid active trace context at connection entry, such as a FastAPI
connection context. If none exists, extract W3C trace context from the
case-insensitive handshake headers. Keep it active for the connection's child
operations and detach it during cleanup. Binary stream spans use rpckit stream
attributes, since raw byte streams are not JSON-RPC invocations.

Follow the current JSON-RPC semantic conventions for RPC attributes, including
`rpc.system.name="jsonrpc"`, `jsonrpc.protocol.version="2.0"`, and error status.
Those conventions are still under development, so document the supported
convention version. Registered method names provide a bounded allowlist;
unknown names must not enter metric labels or span names. Concrete attribute
and metric names belong in the implementation review.
[OpenTelemetry JSON-RPC conventions](https://opentelemetry.io/docs/specs/semconv/rpc/json-rpc/)

Use endpoint names, path templates, registered operations, directions, and
bounded outcome categories for metric dimensions. Do not automatically record
payloads, headers, query strings, path variable values, or close reasons.
`record_exceptions=True` explicitly enables exception events; error category
and status remain available with it disabled. Request IDs never become metric
labels. OTEL log exporting and client-side propagation are follow-up features.

The browser WebSocket API exposes URL and subprotocol arguments, without a
custom-header argument.
[WebSockets Standard](https://websockets.spec.whatwg.org/#the-websocket-interface)
The first version provides server-side traces with any available active
context; distributed client-to-server traces require a separate client
propagation design. It introduces no JSON-RPC fields or binary framing changes.

## Implementation checks after API agreement

Add focused tests for task context isolation, batch siblings, notifications,
rejected handshakes, FastAPI context dependency failures, exception mapping,
normal and abnormal disconnects, and cancellation during setup and execution.
Verify cleanup when generators or DI scopes fail during teardown.

For streams, verify exact counts for uploads, generator downloads, and duplex
`RpcBinaryOutput` writes. Cover zero-length binary messages, input end, invalid
frames, and failed sends. Use OTEL in-memory span and metric exporters to verify
parents, outcomes, and metric values while a stream is still open. Test both
the FastAPI adapter and the in-memory transport, plus imports without OTEL.

## Decisions for discussion

The recommended starting point is `middleware=[OpenTelemetry()]`, with scope
hooks and the existing observer callbacks. A smaller alternative adds the
scope hooks directly to `RpcObserver` and uses `observer=OpenTelemetry()`;
that preserves a smaller API but needs an explicit observer composition story.

Agree on these choices before implementing:

1. Add the separate middleware surface, or extend the existing observer?
2. Keep OTEL opt-in through `pyrpckit[otel]` and one registration?
3. Keep one span per stream, frame metrics, and optional connection spans as
   the initial defaults?
4. Start with server-side traces and metrics, then design generated-client
   propagation separately?
