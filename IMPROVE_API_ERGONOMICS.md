# Vorschlag: schlanke Server-API für PyRPC Kit

Status: MVP implementiert.

## Zielbild

RPC-Handler sind freie Funktionen. Der Router gruppiert sie wie ein
`APIRouter`, besitzt aber keinen Anwendungszustand:

```python
from pyrpckit import Inject, RpcRouter

session_rpc = RpcRouter(
    namespace="session",
    server="session-channel",
)


@session_rpc.method()
async def sync(
    service: Inject[SessionService],
    image_urls: Inject[SessionImageUrlResolver],
    coordinator: Inject[SessionRunCoordinator],
    connection: Inject[SessionConnection],
) -> SessionSyncResult:
    snapshot = await service.get_by_id(
        session_id=connection.session_id,
        user_id=connection.user_id,
    )
    urls = await image_urls.resolve(session=snapshot)
    return SessionSyncResult(
        session=to_session_response(snapshot, image_urls_by_storage_key=urls),
        active_run=await coordinator.status(
            session_id=connection.session_id,
        ),
    )


@session_rpc.method("message.send")
async def send_message(
    params: MessageSendParams,
    runs: Inject[SessionMessageRuns],
    connection: Inject[SessionConnection],
) -> RunAccepted:
    return await runs.start(
        session_id=connection.session_id,
        user_id=connection.user_id,
        command=params,
    )
```

Normale Parameter gehören zum JSON-RPC-Vertrag. `Inject[T]` ist ein
serverseitiger Parameter und erscheint nicht in OpenRPC.

## Dependency-Abstraktion

PyRPC Kit besitzt die DI-Syntax, aber kein DI-System:

```python
from contextlib import AbstractAsyncContextManager
from typing import Annotated, Protocol


class _Inject:
    pass


type Inject[T] = Annotated[T, _Inject()]


class RpcResolver(Protocol):
    async def resolve[T](self, dependency: type[T]) -> T: ...


class RpcScope(Protocol):
    def __call__(
        self,
        resolver: RpcResolver,
    ) -> AbstractAsyncContextManager[RpcResolver]: ...
```

PyRPC Kit entscheidet nur:

- welche Parameter Dependencies sind;
- welcher Typ aufgelöst werden muss;
- wann der Call-Scope betreten und verlassen wird.

Der konkrete Resolver besitzt Provider, Graph, Caching, Lifetimes und Cleanup.

## Call-Scope

`call_scope` bedeutet: eine neue Scope-Instanz pro RPC-Invocation. Der Name ist
absichtlich unabhängig von HTTP, WebSocket, stdio, IPC oder Queues.

Er ist der Default:

```python
router = RpcRouter(namespace="session")
```

Die explizite Form bleibt möglich:

```python
from pyrpckit import call_scope

router = RpcRouter(
    namespace="session",
    scope=call_scope,
)
```

Der Dispatcher führt sinngemäß aus:

```python
async with call_scope(connection_resolver) as resolver:
    dependencies = {
        parameter.name: await resolver.resolve(parameter.dependency)
        for parameter in method.injected_parameters
    }
    return await method.handler(*wire_arguments, **dependencies)
```

Parallele Calls und Mitglieder eines JSON-RPC-Batches erhalten getrennte Scopes.
Der Scope wird nach Resultat oder Fehler geschlossen.

## Dishka-Adapter

Dishka ist eine optionale Runtime-Implementation:

```python
from dishka import AsyncContainer


class DishkaResolver:
    def __init__(self, container: AsyncContainer) -> None:
        self._container = container

    async def resolve[T](self, dependency: type[T]) -> T:
        return await self._container.get(dependency)
```

Der Adapter bildet PyRPC Kits `call_scope` auf Dishkas `REQUEST`-Scope ab. Bei
WebSockets ist ein Dishka-`SESSION`-Container dessen Parent.

`SessionConnection` ist ebenfalls eine normale Dependency. `serve()` nimmt den
Wert entgegen und die Runtime stellt ihn dem Connection-Resolver bereit:

```python
@dataclass(frozen=True, slots=True)
class SessionConnection:
    session_id: UUID
    user_id: UUID
```

Ob der Adapter diesen Wert in Dishkas Context-Map, SESSION-Scope oder einen
vorgeschalteten Resolver legt, bleibt für den Handler unsichtbar.

## WebSocket-DX

Der normale FastAPI-Endpunkt enthält keine Transport-Schleife und keine
DI-Infrastruktur:

```python
router = APIRouter(prefix="/sessions")


@router.websocket("/{session_id}/rpc")
async def session_rpc_endpoint(
    websocket: WebSocket,
    session_id: UUID,
    user_id: AuthenticatedUserId,
) -> None:
    await SESSION_RPC_APP.serve(
        websocket,
        context=SessionConnection(
            session_id=session_id,
            user_id=user_id,
        ),
    )
```

Transport, Resolver, Error-Mapping, Notifications und operative Limits werden
einmalig an `SESSION_RPC_APP` konfiguriert. `pyrpckit.fastapi` stellt dafür eine
ausführbare Runtime-Fassade über dem transportagnostischen `RpcApp` bereit.

Die Runtime übernimmt:

- WebSocket-Handshake und Subprotocol;
- JSON-Decoding und Encoding;
- Parse Errors und JSON-RPC-Batches;
- begrenzte Call-Parallelität;
- serialisierte Writes;
- Multiplexing von Responses und Notifications;
- Queue-Limits, Backpressure, Cancellation und Shutdown.

## Typisierte Notifications

Eine Notification-Deklaration registriert Vertrag und asynchrone Quelle gemeinsam:

```python
@session_rpc.notification(
    "event",
    payload=SessionRpcEvent,
)
async def session_notifications(
    coordinator: Inject[SessionRunCoordinator],
    connection: Inject[SessionConnection],
) -> AsyncIterator[SessionRpcEvent]:
    async with coordinator.subscribe(
        session_id=connection.session_id,
    ) as events:
        async for event in events:
            yield event
```

Der Router registriert Vertrag und Stream-Quelle gemeinsam. Die WebSocket-Runtime
startet die Quelle automatisch pro Verbindung, validiert ihre Payloads und baut
weder in PromptStars noch in der Quelle `RpcNotification(...)` oder den
Methodennamen `"session.event"` von Hand.

## Keine Controller-Klassen

V1 unterstützt ausschließlich freie Funktionen. Damit entfallen:

- Controller-Konstruktion und -Lifetime;
- Constructor-Injection;
- Owner- und Descriptor-Erkennung;
- `RpcMountBinding` und `RpcRouterMount.bind(...)`;
- Mehrdeutigkeiten bei Handlerinstanzen und mehrfachen Mounts.

Ein Router-Mount verändert nur Namen, Tags, Server- und Scope-Metadaten einer
freien Funktion.

## Background Jobs

Ein Call-Scope endet mit der RPC-Antwort. Eine darin aufgelöste Dependency darf
nicht in einen länger laufenden Task entkommen.

`message.send` sollte deshalb einen Application-Service wie
`SessionMessageRuns` aufrufen. Dieser besitzt die fachliche Run-Lifetime und
öffnet bei Bedarf einen eigenen Job-Scope. Run-IDs, Reconnect, Event-Replay und
die Regel „nur ein aktiver Run pro Session“ bleiben außerhalb von PyRPC Kit.

## Paketgrenzen

```text
pyrpckit
    RpcRouter / RpcApp / Dispatch
    Inject[T]
    RpcResolver / RpcScope / call_scope
    JSON-RPC codec und batch handling
    typisierte notification handles

pyrpckit.fastapi
    serve runtime
    websocket lifecycle
    concurrency und backpressure

pyrpckit.dishka
    DishkaResolver
    SESSION -> REQUEST call-scope adapter
    connection-context bridge
```

Der Core importiert weder FastAPI noch Dishka.

## MVP

1. Klassenbasierte Handler und deren Binding entfernen.
2. `Inject[T]`, `RpcResolver`, `RpcScope` und `call_scope` implementieren.
3. Signaturanalyse und OpenRPC um server-only Parameter ergänzen.
4. Dishka-Resolver und Connection-Context-Bridge implementieren.
5. Typisierte Notification-Builder ergänzen.
6. JSON-RPC-Codec einschließlich Parse Errors und Batches zentralisieren.
7. `pyrpckit.fastapi` mit `SESSION_RPC_APP.serve(...)` implementieren.

JSON-RPC-Regeln gehören in den Core, da die Spezifikation transportagnostisch ist:
<https://www.jsonrpc.org/specification>

Dishkas `SESSION`-/`REQUEST`-Hierarchie für WebSockets entspricht der benötigten
Connection-/Call-Lifetime:
<https://dishka.readthedocs.io/en/stable/integrations/starlette.html#websockets>
