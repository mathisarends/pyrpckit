# pyrpckit 0.6 — Neue Server-API

Status: Spec, bereit zur Umsetzung. Zielversion: **0.6.0** (Breaking Release
gegenüber 0.5.0). 0.6.0 ist noch unveröffentlicht; die bereits auf dem Branch
begonnene Binary-Stream-Arbeit geht in dieser Spec auf und wird gemeinsam
released.

Diese Spec ersetzt die Kompositions-, Serving- und Fehler-API von pyrpckit. Sie
ist so geschrieben, dass sie ohne Rückfragen umgesetzt werden kann: jede
öffentliche Signatur, jedes Laufzeitverhalten, jede Wire- und Contract-Änderung,
die Codegen-Folgen, die Tests und der Changelog-Text sind festgelegt.
**Rückwärtskompatibilität ist ausdrücklich kein Ziel.** Alte APIs werden
entfernt, nicht deprecated. Jede Änderung landet im Changelog (§14).

---

## 0. Leitprinzipien

1. **Channel = was, Socket = wo, Adapter = wie.**
   `RpcChannel` gruppiert Operationen (Methoden, Events, Binary-Streams) fachlich und weiß nichts
   über Verbindungen. `RpcService` beschreibt die Socket-Topologie: welcher Pfad
   welche Channels ausliefert und welcher Connect-Hook davor läuft. Frameworks
   wie FastAPI sind reine Adapter hinter dem Port `RpcSocket`.
2. **Der Core bleibt transportagnostisch — auch gegenüber HTTP und WebSocket.**
   Kein Import von FastAPI, Starlette oder WebSocket-Bibliotheken, und keine
   HTTP-Status- oder WebSocket-Close-Codes in Core-Typen. Der Core definiert nur
   den Port `RpcSocket` und die semantischen Enums `RpcRejection` und
   `RpcConnectionClose`. Die Zuordnung zu HTTP-Status bzw. WebSocket-Close-Codes
   ist Sache der Adapter (`pyrpckit.fastapi`, `pyrpckit.websocket`).
3. **Drei Fehlerarten, drei Mechanismen, keine Überlappung.**
   - `RpcError` → JSON-RPC-Fehlerantwort, die Verbindung bleibt offen.
   - `raise ConnectionRejected(...)` → **ausschließlich** im Connect-Hook: der
     Handshake wird nicht angenommen.
   - `await connection.close(...)` → **ausschließlich** nach `accept()`: eine
     bestehende Verbindung wird beendet, ohne JSON-RPC-Antwort.
4. **`RpcConnection` ist rein semantisch.** Sie gibt weder den Socket noch
   Framework-Objekte heraus. Wer den FastAPI-`WebSocket` braucht, schreibt eine
   manuelle FastAPI-Route und hat ihn dort (§10.3).
5. **Eine offensichtliche Art.** Pro Aufgabe genau ein Weg. Aliase und
   Kompatibilitäts-Shims gibt es nicht.
6. **Der Contract rendert nur, was der Service beschreibt.** `rpc.contract()`
   fügt keine API-Struktur hinzu (keine Streams, keine Methoden), sondern nur
   Deployment-Angaben (`title`, `base_url`, Variablen-Defaults).
7. **Definition ist importierbar ohne Laufzeit.** `rpc.py` kann von Codegen und
   Contract-Export importiert werden, ohne Container, Datenbank oder Server.
   Laufzeitkonfiguration (`resolver`, `context`, `error_mapper`, `limits`) wird
   erst beim Serven übergeben.

---

## 1. Die neue API auf einen Blick

### 1.1 Definition (`app/rpc.py`)

```python
from collections.abc import AsyncIterator
from typing import Literal

from pyrpckit import (
    ConnectionRejected,
    Inject,
    RpcService,
    RpcChannel,
    RpcConnection,
    RpcConnectionClose,
    RpcError,
    RpcModel,
    RpcRejection,
)


# --- Modelle ------------------------------------------------------------------

class CreateSession(RpcModel):
    project_id: str


class StopSession(RpcModel):
    session_id: str


class Session(RpcModel):
    id: str
    project_id: str


class SessionChanged(RpcModel):
    type: Literal["session.changed"] = "session.changed"
    session_id: str
    state: str


class ProjectNotFound(RpcModel):
    project_id: str


# --- Fehler -------------------------------------------------------------------

class ProjectNotFoundError(RpcError):
    message = "Project not found"
    details: ProjectNotFound          # code = "project_not_found" (abgeleitet)


class UnauthorizedError(RpcError):
    message = "Not allowed"           # code = "unauthorized", keine details


# --- Channels -----------------------------------------------------------------

control = RpcChannel("control", raises=[UnauthorizedError])


@control.method(raises=[ProjectNotFoundError])
async def create_session(
    params: CreateSession,
    sessions: Inject[SessionService],
    user: Inject[User],
) -> Session:
    project = await sessions.find_project(params.project_id, owner=user.id)
    if project is None:
        raise ProjectNotFoundError(project_id=params.project_id)
    return await sessions.create(project)


@control.method()
async def stop_session(
    params: StopSession,
    sessions: Inject[SessionService],
) -> None:
    await sessions.stop(params.session_id)


events = RpcChannel("events", namespace="session")


@events.event("changed")
async def session_changed(
    connection: Inject[RpcConnection],
    publisher: Inject[EventPublisher],
) -> AsyncIterator[SessionChanged]:
    async for event in publisher.subscribe(connection.path_params["session_id"]):
        yield event


# --- Verbindungen -------------------------------------------------------------

async def authenticate(connection: RpcConnection, auth: Inject[AuthService]) -> User:
    token = connection.headers.get("Authorization")
    if not token:
        raise ConnectionRejected(RpcRejection.UNAUTHORIZED, "Authentication required")
    user = await auth.user_for_token(token)
    if user is None:
        raise ConnectionRejected(RpcRejection.UNAUTHORIZED, "Invalid token")
    return user                        # ab jetzt injizierbar als Inject[User]


@control.method()
async def refresh(
    connection: Inject[RpcConnection],
    user: Inject[User],
) -> None:
    if user.session_expired():
        await connection.close(
            RpcConnectionClose.POLICY_VIOLATION,
            reason="Session expired",
        )


# --- Topologie ----------------------------------------------------------------

rpc = RpcService(version=1, connect=authenticate)

rpc.socket("/rpc", control)
rpc.socket("/sessions/{session_id}/events", events)
```

### 1.2 FastAPI-Server (`app/main.py`)

```python
from fastapi import FastAPI
from pyrpckit.dishka import DishkaResolver
from pyrpckit.fastapi import create_router

from app.container import container
from app.rpc import rpc

app = FastAPI()
app.include_router(create_router(rpc, resolver=DishkaResolver(container)))
```

### 1.3 Contract (`app/contract.py`)

```python
from pyrpckit import ServerVariable

from app.rpc import rpc

contract = rpc.contract(
    title="Session API",
    base_url="wss://api.example.com",
    variables={"session_id": ServerVariable(default="demo-session")},
)
```

```toml
# rpcgen.toml
[contract]
source = "app.contract:contract"
output = "schema/session.openrpc.json"
```

### 1.4 Ohne Framework

```python
await rpc.serve(MySocket(raw_connection), resolver=resolver)
```

`rpc.serve()` wählt den Endpoint anhand von `socket.handshake.path`. Die
Rejection- und Close-Zuordnung eines reinen WebSocket-Adapters kommt aus
`pyrpckit.websocket` (§5.6).

### 1.5 Binary-Streams im Namespace

```python
browser = RpcChannel("browser")

screencast = RpcChannel("screencast", namespace="browser.screencast")


@browser.method()
async def navigate(params: Navigate, pages: Inject[PageService]) -> None:
    await pages.navigate(params.url)


@screencast.method()
async def start(
    params: StartScreencast,
    screencasts: Inject[ScreencastService],
) -> Screencast:
    return await screencasts.start(params)


@screencast.stream(content_type="image/jpeg")
async def frames(
    connection: Inject[RpcConnection],
    screencasts: Inject[ScreencastService],
) -> AsyncIterator[bytes]:
    async for frame in screencasts.frames(connection.path_params["browser_id"]):
        yield frame


rpc = RpcService(connect=authenticate)
rpc.socket("/browsers/{browser_id}/rpc", browser, screencast)
rpc.stream("/browsers/{browser_id}/screencast", frames)
```

Channel und Decorator beschreiben, **was** es gibt; `rpc.socket()` und
`rpc.stream()` legen fest, **wo** es liegt. Beide Endpoints laufen über
denselben `RpcSocket`-Port, denselben Connect-Hook und dieselben
Close-Semantiken, und `create_router()` registriert beide.

Logische API und Wire-Ebene:

```
browser.navigate            JSON-RPC                 /browsers/{browser_id}/rpc
browser.screencast.start    JSON-RPC                 /browsers/{browser_id}/rpc
browser.screencast.frames   Binary, Server → Client  /browsers/{browser_id}/screencast
```

Generierter Client:

```python
await client.browser.navigate(url="https://example.com")
cast = await client.browser.screencast.start(quality=80)

async with client.browser.screencast.frames(browser_id="b-1") as frames:
    async for frame in frames:
        render(frame)
```

---

## 2. Begriffe

| Begriff | Typ | Bedeutung |
|---|---|---|
| Channel | `RpcChannel` | Fachliche Gruppe von Methoden, Events und Binary-Streams mit gemeinsamem Namespace, Tags, `raises` und Resolver-Scope. Endpoint-unabhängig. |
| Service | `RpcService` | Die gesamte RPC-Oberfläche einer Anwendung: Protokollversion, Default-Connect-Hook, alle Endpoints. Quelle für Contract und Serving. |
| Endpoint | `RpcEndpoint` | Ein JSON-RPC-Socket-Pfad mit seinen Channels, höchstens einem Connect-Hook und optionalem Subprotocol. Wird im Contract ein OpenRPC-`server`. |
| Stream-Endpoint | `RpcStreamEndpoint` | Ein Socket-Pfad, der genau einen Binary-Stream ausliefert. Gleicher Connect-Hook-, Subprotocol- und Close-Mechanismus wie `RpcEndpoint`. |
| Socket (Port) | `RpcSocket` | Protokoll, das ein Adapter für eine konkrete Verbindung implementiert. |
| Handshake | `RpcHandshake` | Unveränderliche Daten des Verbindungsaufbaus (Pfad, Header, Query, Subprotocols, Client). |
| Connection | `RpcConnection` | Semantische Sicht auf eine Verbindung: Pfad-/Query-Parameter, Header, Client, `close()`. Kein Zugriff auf Socket oder Framework. Injizierbar. |
| Connect-Hook | Funktion | Einfacher Connection-Context-Builder: läuft vor `accept()`, darf Dependencies konsumieren, lehnt mit `ConnectionRejected` ab oder liefert **einen** Wert für `Inject[...]`. Kein eigenes Lifecycle- oder DI-System. |
| Binary-Stream | Funktion | Per `@channel.stream()` dekorierter async generator, der Binärframes vom Server zum Client liefert. Name im Namespace des Channels; von pyrpckit serviert. |
| Rejection | `RpcRejection` | Semantischer Grund, warum ein Handshake nicht angenommen wurde. |
| Close-Grund | `RpcConnectionClose` | Semantischer Grund, warum eine angenommene Verbindung endet. |

**Namenswahl.** `RpcService` statt `RpcRouter` oder `RpcApi`: `RpcRouter` war
ein entferntes Pre-v1-Symbol (siehe `tests/test_public_api.py`) und kollidiert
gedanklich mit FastAPIs `APIRouter`, den `create_router()` erzeugt. `RpcService`
beschreibt, was das Objekt ist: der angebotene RPC-Dienst mit seinen Endpoints,
aus dem Server und Contract abgeleitet werden. `rpc.socket(path, *channels)`
statt `mount()` oder `RpcRouter(endpoints=[RpcEndpoint(...)])`: ein Aufruf pro
Socket liest sich wie die Topologie selbst, und der Rückgabewert (`RpcEndpoint`)
ist direkt nutzbar.

---

## 3. `RpcChannel`

Datei: `pyrpckit/channel.py` (ersetzt `pyrpckit/app.py` und `pyrpckit/router.py`).

### 3.1 Signatur

```python
class RpcChannel:
    def __init__(
        self,
        name: str,
        /,
        *,
        namespace: str | None = None,
        tags: Iterable[str] = (),
        raises: Iterable[type[RpcError]] = (),
        resolver_scope: RpcResolverScope = call_scope,
    ) -> None: ...

    name: str                                   # property
    namespace: str                              # property
    tags: tuple[str, ...]                       # property
    raises: tuple[type[RpcError], ...]          # property
    resolver_scope: RpcResolverScope            # property
    routes: tuple[RpcRoute, ...]                # property
    events: tuple[RpcNotificationDefinition, ...]  # property
    streams: tuple[RpcStreamDefinition, ...]    # property
    protocol: RpcProtocol                       # property, friert ein

    @overload
    def method(self, function: F, /) -> F: ...
    @overload
    def method(
        self,
        name: str | None = None,
        /,
        *,
        summary: str | None = None,
        tags: Iterable[str] = (),
        raises: Iterable[type[RpcError]] = (),
    ) -> Callable[[F], F]: ...

    @overload
    def event(self, function: F, /) -> F: ...
    @overload
    def event(
        self,
        name: str | None = None,
        /,
        *,
        payload: Any = <inferred>,
        summary: str | None = None,
        tags: Iterable[str] = (),
    ) -> Callable[[F], F]: ...

    @overload
    def stream(self, function: F, /) -> F: ...
    @overload
    def stream(
        self,
        name: str | None = None,
        /,
        *,
        content_type: str = "application/octet-stream",
        summary: str | None = None,
        tags: Iterable[str] = (),
    ) -> Callable[[F], F]: ...

    def freeze(self) -> RpcProtocol: ...

    def server(
        self,
        *,
        context: object | Mapping[type[Any], object] | None = None,
        resolver: RpcResolverLike | None = None,
        error_mapper: RpcErrorMapper | None = None,
    ) -> RpcServer: ...
```

### 3.2 Regeln

- `name` ist positional und Pflicht; nicht leer; gleiche Validierung wie ein
  Namespace-Segment (`[A-Za-z_][A-Za-z0-9_-]*`). Er dient Diagnosen und der
  Eindeutigkeit innerhalb einer `RpcService`.
- `namespace=None` (Default) → Namespace = `name`. `namespace=""` → Methoden
  liegen ohne Präfix auf der Wurzel. Andere Werte: gepunktete Namen wie heute
  (`"browser.tabs"`).
- Wire-Name einer Methode: `join_rpc_name(namespace, method_name)`;
  `method_name` = explizites `name` oder `function.__name__`.
- `@channel.method` **und** `@channel.method()` sind beide erlaubt. Der
  bisherige Fehler „must use parentheses“ entfällt. Wird ein Nicht-String,
  Nicht-Funktions-Argument übergeben → `ProtocolDefinitionError`.
  README, Beispiele, Docstrings und Tests außerhalb dieses Features schreiben
  **immer** `@channel.method()` / `@channel.event()` / `@channel.stream()` mit
  Klammern.
- Handler-Regeln bleiben unverändert: async freie Funktion, höchstens ein
  positionaler Pydantic-Parameter, beliebig viele `Inject[T]`, Return-Annotation
  Pflicht.
- `raises` einer Methode = `channel.raises + method.raises`, reihenfolgetreu
  dedupliziert. Jeder Eintrag wird mit `declared_error()` validiert (§7.2).
- `tags` einer Methode = `channel.tags + method.tags`, dedupliziert.
- `@channel.event`:
  - `name` optional, Default `function.__name__`.
  - `payload` optional. Wird es weggelassen, wird es aus der Return-Annotation
    `AsyncIterator[T]` / `AsyncGenerator[T, None]` abgeleitet. Wird es
    angegeben, muss es mit `T` übereinstimmen (bisheriges Verhalten).
  - Die Funktion ist ein async generator, Parameter nur `Inject[T]`.
- `freeze()` materialisiert das `RpcProtocol` und verbietet weitere
  Registrierungen (idempotent, bestehende Fehlermeldung beibehalten, aber
  „by RpcService“ statt „by RpcContract.from_channels()“).
- `RpcChannel.server()` bleibt für Tests und nachrichtenbasierte Transports
  ohne Verbindung. Connect-Hooks laufen dort nicht.

### 3.3 Binary-Streams: `@channel.stream()`

Binary-Streams gehören fachlich in den Namespace ihres Channels
(`browser.screencast.frames` neben `browser.screencast.start`), laufen auf der
Wire-Ebene aber über einen eigenen WebSocket mit Binärframes. Authoring und
Codegen präsentieren sie im selben Namespace; der Contract unterscheidet
weiterhin Methoden, Events und Binary-Streams.

pyrpckit **servt** Binary-Streams. Deshalb ist `stream` ein Decorator mit
Handler. In 0.6.0 gibt es ausschließlich die Richtung **Server → Client**.

```python
@screencast.stream(content_type="image/jpeg")
async def frames(
    connection: Inject[RpcConnection],
    screencasts: Inject[ScreencastService],
) -> AsyncIterator[bytes]:
    async for frame in screencasts.frames(connection.path_params["browser_id"]):
        yield frame
```

Regeln (Fehler → `ProtocolDefinitionError`):

- `@channel.stream` und `@channel.stream()` sind erlaubt (wie `method`).
- `name` optional, Default `function.__name__`; validiert wie ein
  Methodenname. Wire-Name: `join_rpc_name(namespace, name)`.
- Namen sind im Channel eindeutig über Methoden, Events und Streams hinweg.
- Handler: async generator als freie Funktion.
  - Return-Annotation exakt `AsyncIterator[bytes]` oder
    `AsyncGenerator[bytes, None]`. Alles andere → Fehler („binary streams must
    yield bytes; only server-to-client streams are supported“).
  - Parameter ausschließlich `Inject[T]` (wie bei Events). Ein
    Pydantic-Parameter oder ein `AsyncIterator[bytes]`-Eingang → Fehler mit
    Hinweis, dass Client→Server-Streams nicht unterstützt werden.
- `content_type`: nicht leer.
- `tags` = `channel.tags + tags`, dedupliziert. `summary` Default: erste Zeile
  des Docstrings.
- Der Decorator gibt die Funktion unverändert zurück und markiert sie privat
  (`function.__pyrpckit_stream__ = (channel, local_name)`), damit
  `rpc.stream(path, frames)` sie zuordnen kann.
- Registrierung nach `freeze()` → Fehler wie bei Methoden.
- `RpcServer` und `channel.server()` ignorieren Streams.

Definitionsobjekt (`pyrpckit/protocol.py`):

```python
@dataclass(frozen=True, slots=True)
class RpcStreamDefinition:
    name: str                                   # vollständiger Wire-Name
    function: FunctionType
    content_type: str
    summary: str | None
    tags: tuple[str, ...]
    injected_parameters: tuple[RpcInjectedParameter, ...]
    resolver_scope: RpcResolverScope            # vom Channel
    server: str | None = None                   # Stream-Endpoint-Name, gesetzt in RpcService.freeze()
```

`RpcProtocol` erhält `streams: tuple[RpcStreamDefinition, ...]`. Das
`BinaryStream`-Dataclass und `BinaryStreamDirection` aus dem unveröffentlichten
Branch-Stand entfallen ersatzlos (kein öffentlicher Export).

### 3.4 Entfernt

- `RpcModule` (Klasse und Export). Channels sind jetzt selbst
  endpoint-unabhängig; mehrere Channels werden über `rpc.socket(path, a, b, c)`
  kombiniert.
- `RpcChannel(modules=...)`, `RpcChannel.include()`.
- `RpcChannel(version=...)` → `RpcService(version=...)`.
- `@method(errors=...)` → `raises=...`.

`RpcRoute`, `join_rpc_name`, `normalize_namespace`, `normalize_tags` und
`_docstring_summary` ziehen nach `pyrpckit/channel.py`. `router.py` wird
gelöscht.

---

## 4. `RpcService` und `RpcEndpoint`

Datei: `pyrpckit/service.py`.

### 4.1 Signatur

```python
type ConnectHook = Callable[..., Awaitable[Any]]


class RpcService:
    def __init__(
        self,
        *,
        version: int = 1,
        connect: ConnectHook | None = None,
    ) -> None: ...

    version: int                                # property
    endpoints: tuple[RpcEndpoint | RpcStreamEndpoint, ...]  # property, Registrierungsreihenfolge

    def socket(
        self,
        path: str,
        /,
        *channels: RpcChannel,
        name: str | None = None,
        connect: ConnectHook | None = None,       # None → Hook des Service
        subprotocol: str | None = None,
        summary: str | None = None,
    ) -> RpcEndpoint: ...

    def stream(
        self,
        path: str,
        stream: Callable[..., AsyncIterator[bytes]],
        /,
        *,
        name: str | None = None,
        connect: ConnectHook | None = None,       # None → Hook des Service
        subprotocol: str | None = None,
        summary: str | None = None,
    ) -> RpcStreamEndpoint: ...

    def endpoint(self, name: str) -> RpcEndpoint | RpcStreamEndpoint: ...   # KeyError-Text: bekannte Namen
    def match(
        self, path: str
    ) -> tuple[RpcEndpoint | RpcStreamEndpoint, dict[str, str]] | None: ...

    def freeze(self) -> RpcProtocol: ...

    async def serve(
        self,
        socket: RpcSocket,
        *,
        resolver: RpcResolverLike | None = None,
        context: object | Mapping[type[Any], object] | None = None,
        error_mapper: RpcErrorMapper | None = None,
        limits: RpcLimits | None = None,
        root_path: str = "",
    ) -> None: ...

    def contract(
        self,
        *,
        title: str,
        base_url: str,
        description: str = "Typed JSON-RPC API.",
        variables: Mapping[str, ServerVariable] | None = None,
    ) -> RpcContract: ...


@dataclass(frozen=True, slots=True, eq=False)
class RpcEndpoint:
    service: RpcService
    name: str
    path: str
    channels: tuple[RpcChannel, ...]
    connect: ConnectHook | None                 # effektiver Hook: socket-eigener, sonst der des Service
    subprotocol: str | None
    summary: str | None
    path_variables: tuple[str, ...]             # in Reihenfolge des Auftretens

    @property
    def protocol(self) -> RpcProtocol: ...           # freeze der API, gefiltert

    def match(self, path: str) -> dict[str, str] | None: ...

    async def serve(
        self,
        socket: RpcSocket,
        *,
        resolver: RpcResolverLike | None = None,
        context: object | Mapping[type[Any], object] | None = None,
        error_mapper: RpcErrorMapper | None = None,
        limits: RpcLimits | None = None,
    ) -> None: ...

    def server(
        self,
        *,
        context: object | Mapping[type[Any], object] | None = None,
        resolver: RpcResolverLike | None = None,
        error_mapper: RpcErrorMapper | None = None,
    ) -> RpcServer: ...


@dataclass(frozen=True, slots=True, eq=False)
class RpcStreamEndpoint:
    service: RpcService
    name: str
    path: str
    stream: RpcStreamDefinition                 # property; aufgelöst beim freeze()
    connect: ConnectHook | None                 # effektiver Hook: stream-eigener, sonst der des Service
    subprotocol: str | None
    summary: str | None
    path_variables: tuple[str, ...]

    def match(self, path: str) -> dict[str, str] | None: ...

    async def serve(
        self,
        socket: RpcSocket,
        *,
        resolver: RpcResolverLike | None = None,
        context: object | Mapping[type[Any], object] | None = None,
        limits: RpcLimits | None = None,
    ) -> None: ...
```

`rpc.serve(socket, error_mapper=...)` reicht `error_mapper` nur an
`RpcEndpoint` weiter; Stream-Endpoints erzeugen keine JSON-RPC-Fehler.

### 4.2 Validierung in `socket()` und `stream()` (eager, `ProtocolDefinitionError`)

- API ist nicht eingefroren.
- `path` beginnt mit `/`; kein abschließender `/` außer bei `"/"`; keine
  leeren Segmente; kein Query oder Fragment.
- Pfadvariablen: `{name}` als ganzes Segment oder Segmentteil, `name` matcht
  `[A-Za-z_][A-Za-z0-9_]*`, eindeutig pro Pfad. Konverter wie `{id:int}` sind
  nicht erlaubt.
- Mindestens ein Channel; jeder ist ein `RpcChannel`.
- Ein Channel darf in einer API nur **einmal** gemountet werden (über alle
  Endpoints). Grund: OpenRPC-Methoden werden in pyrpckit genau einem Server
  zugeordnet (`codegen/ir.py::_server_reference`).
- Channel-Namen sind API-weit eindeutig.
- `name` Default: letztes statisches Pfadsegment ohne Variablen
  (`/sessions/{session_id}/control` → `control`, `/rpc` → `rpc`). Ist keins
  vorhanden (`/`, `/{tenant}`) → Fehler „pass name=...“. Erlaubte Zeichen
  `[A-Za-z][A-Za-z0-9_-]*`. Eindeutig pro API.
- `path` eindeutig pro API. Zwei Pfade, die sich nur in Variablennamen
  unterscheiden (`/a/{x}` vs. `/a/{y}`), gelten als Duplikat.
- `subprotocol`: nicht leerer String oder `None`.
- Der Connect-Hook wird sofort analysiert (§5.2); Signaturfehler fallen hier
  bzw. in `RpcService(...)` für den Service-Hook.
- Pfad-, Namens- und Subprotocol-Regeln gelten für `stream()` identisch;
  Pfade und Namen sind über Sockets **und** Streams hinweg eindeutig.
- `stream()`: das zweite Argument muss eine mit `@channel.stream()` dekorierte
  Funktion sein, sonst Fehler („rpc.stream() expects a function decorated with
  @channel.stream()“). Jeder Stream darf nur **einmal** gemountet werden.
- Der Channel eines Streams muss nicht per `socket()` gemountet sein; ein
  Channel kann ausschließlich Streams enthalten.
- Nicht gemountete Streams sind erlaubt und erscheinen weder im Contract noch
  im Router.

### 4.3 `freeze()`

Idempotent. Wird implizit von `contract()`, `serve()`, `endpoint.serve()`,
`endpoint.server()`, `endpoint.protocol` und `create_router()` aufgerufen.

1. Friert alle gemounteten Channels und die Channels gemounteter Streams ein.
2. Baut ein kombiniertes `RpcProtocol(version=self.version)`, in dem jede
   Methode und jedes Event `server=endpoint.name` trägt (Logik aus dem heutigen
   `contract._combined_protocol` übernehmen, inklusive der Request-Namens-
   Disambiguierung).
3. Prüft API-weit eindeutige Namen über Methoden, Events und Streams
   (`Duplicate RPC name: x (channels 'a' and 'b')`). Zusätzlich darf kein Name
   zugleich Blatt und Namespace sein (Stream `browser.screencast` neben Methode
   `browser.screencast.start` → `RPC name 'browser.screencast' is both an
   operation and a namespace`), weil generierte Clients daraus einen
   Namespace-Baum bauen. Geprüft werden nur gemountete Operationen.
   Gemountete Streams erhalten `server=<Stream-Endpoint-Name>` und landen in
   `RpcProtocol.streams`.
4. Prüft API-weit eindeutige Error-Codes: zwei **verschiedene** `RpcError`-
   Klassen mit gleichem `code` → Fehler.
5. Danach wirft `socket()` `ProtocolDefinitionError("RpcService is frozen ...")`.

`RpcProtocol.version` bleibt; für Channel-Protokolle ist sie immer `1` und ohne
Bedeutung. Autoritativ ist nur das API-Protokoll.

### 4.4 `match()`

`endpoint.match(path)` vergleicht segmentweise gegen die Vorlage und liefert die
Pfadparameter (URL-dekodiert) oder `None`. `rpc.match(path)` probiert Endpoints
in Registrierungsreihenfolge (Sockets und Streams gemeinsam), statische Pfade
vor Pfaden mit Variablen.

---

## 5. Verbindungen

Datei: `pyrpckit/connection.py` (Typen); die Signaturanalyse des Connect-Hooks liegt in `pyrpckit/service.py`.

### 5.1 `RpcConnection`

```python
class RpcConnection:
    endpoint: str                           # Endpoint-Name
    path: str
    path_params: Mapping[str, str]
    query_params: Mapping[str, str]
    headers: Mapping[str, str]              # case-insensitive
    subprotocols: tuple[str, ...]           # vom Client angeboten
    client: tuple[str, int] | None
    closed: bool

    async def close(
        self,
        close: RpcConnectionClose = RpcConnectionClose.NORMAL,
        *,
        reason: str = "",
    ) -> None: ...
```

- Wird vom Core erzeugt, nie vom Nutzer. Konstruktor privat
  (`RpcConnection._create(...)`, `__init__` wirft `TypeError` wie heute
  `RpcServer`).
- **Kein** öffentliches `socket`, `handshake`, `websocket` oder vergleichbares
  Attribut. Socket und Runtime-Zustand liegen in privaten Slots
  (`__slots__`), damit Handler nicht transportabhängig werden können.
  Das ist eine bewusste Designentscheidung, kein Versäumnis.
- `path_params`: `handshake.path_params`, falls nicht leer, sonst
  `endpoint.match(handshake.path)` (bei `rpc.serve` nach Abzug von `root_path`),
  sonst `{}`.
- `headers`: unveränderliches, case-insensitive Mapping
  (`pyrpckit/connection.py::_Headers`), `connection.headers["authorization"]` und
  `["Authorization"]` sind identisch.
- `RpcConnection` erhält der Connect-Hook als plain Parameter; Methoden und
  Events injizieren sie als `Inject[RpcConnection]`.
- `close()` vor `accept()` (also in einem Connect-Hook) ist verboten und raist
  `RuntimeError("RpcConnection.close() is only valid after the connection was
  accepted; raise ConnectionRejected in connect hooks")`.
- `close()` nach `accept()`: Semantik in §6.4. Das ist der **einzige** Weg,
  eine angenommene Verbindung aus Anwendungscode zu beenden.

### 5.2 Connect-Hook

Der Connect-Hook ist ein **einfacher Connection-Context-Builder**. Er darf
Dependencies konsumieren und einen Connection-Context bereitstellen. Mehr
nicht: pyrpckit baut kein zweites DI- oder Lifecycle-System. Ressourcen mit
Verbindungslebensdauer (DB-Verbindungen, Subscriptions, Teardown) gehören in
den Resolver, z. B. in Dishkas `SESSION`-Scope (§11).

```
rpckit                         Resolver (z. B. Dishka)
──────                         ───────────────────────
connection lifecycle ────────→ SESSION scope
                               DB connection
                               resources
                               teardown
```

```python
async def authenticate(
    connection: RpcConnection,
    auth: Inject[AuthService],
) -> User:
    user = await auth.authenticate(connection.headers.get("Authorization"))
    if user is None:
        raise ConnectionRejected(RpcRejection.UNAUTHORIZED, "Invalid credentials")
    return user


rpc = RpcService(connect=authenticate)
rpc.socket("/rpc", control)


@control.method()
async def create(
    params: CreateSession,
    user: Inject[User],
    sessions: Inject[SessionService],
) -> Session: ...
```

**Anzahl und Auswahl.** Pro Endpoint läuft **höchstens ein** Hook:
`socket(connect=...)`, falls gesetzt, sonst `RpcService(connect=...)`, sonst
keiner. Der Socket-Hook **ersetzt** den Service-Hook; es gibt keine Ketten und
keine Reihenfolge. Wer pro Socket unterschiedliche Anforderungen hat, setzt den
Hook pro Socket statt global. Wer mehrere Schritte braucht, ruft sie im Hook
nacheinander auf.

**Signaturregeln** (geprüft in `RpcService(...)` bzw. `rpc.socket(...)`,
Fehler → `ProtocolDefinitionError`):

- Coroutine-Funktion. Sync-Funktionen und async generators → Fehler
  („connect hooks must be async functions; use your resolver for
  connection-scoped resources“).
- Parameter:
  - Annotation exakt `RpcConnection` → erhält die Verbindung.
  - `Inject[T]` → wird über den Resolver aufgelöst.
  - Alles andere, `*args`, `**kwargs`, positional-only und Defaults → Fehler.
- Return-Annotation ist Pflicht: eine konkrete Klasse `T` oder `None`.
  Unions, `Optional`, Generics, `Any` und `RpcConnection` → Fehler („connect
  hooks must provide a concrete type“). Mehrere Werte → eine eigene Klasse
  (z. B. ein `@dataclass ConnectionContext`) zurückgeben.

**Auflösung von `Inject[T]` im Hook:** exakt wie bei einem Methodenaufruf:
statischer `context` und `RpcConnection` zuerst, sonst der Resolver innerhalb
eines `call_scope`, der direkt nach Rückkehr des Hooks, also vor `accept()`,
wieder verlassen wird (§6.2). Hook-Parameter werden nicht aus Rückgabewerten
anderer Hooks befüllt, weil es keine anderen Hooks gibt.

**Ergebnis:** Ein Rückgabewert `T` (nicht `None`) wird unter dem annotierten
Typ `T` in den Verbindungskontext gelegt und ist in Methoden und Events als
`Inject[T]` verfügbar. Zur Laufzeit wird nicht geprüft, ob der Wert eine Instanz
von `T` ist. Enthält der statische `context` bereits `T`, wirft
`serve()` vor dem Hook-Aufruf `ValueError("connect hook provides User, which is
also passed as context")`.

**Fehler im Hook:**

| Ausnahme | Reaktion |
|---|---|
| `ConnectionRejected` | `socket.reject(error.rejection, error.reason)`, `serve()` kehrt normal zurück |
| jede andere `Exception` | `logger.exception(...)`, `socket.reject(RpcRejection.INTERNAL_ERROR, "Internal error")`, normale Rückkehr |
| `CancelledError` | erneut raisen, kein `reject()` |

### 5.3 `ConnectionRejected` und `RpcRejection`

```python
class RpcRejection(StrEnum):
    UNAUTHORIZED = "unauthorized"        # Identität fehlt oder ist ungültig
    FORBIDDEN = "forbidden"              # Identität bekannt, Zugriff verweigert
    NOT_FOUND = "not_found"              # kein Endpoint / Ressource aus dem Pfad existiert nicht
    PROTOCOL_ERROR = "protocol_error"    # Handshake passt nicht (z. B. Subprotocol)
    UNAVAILABLE = "unavailable"          # vorübergehend nicht annehmbar (Überlast, Wartung)
    INTERNAL_ERROR = "internal_error"    # unerwarteter Fehler im Hook


class ConnectionRejected(Exception):
    def __init__(
        self,
        rejection: RpcRejection,
        reason: str = "",
    ) -> None: ...

    rejection: RpcRejection
    reason: str      # Default: aus rejection abgeleitet ("Unauthorized", "Forbidden", ...)
```

```
   Connect-Hook                                  nach accept()
        │                                             │
 raise ConnectionRejected(                  await connection.close(
     RpcRejection.UNAUTHORIZED,                 RpcConnectionClose.POLICY_VIOLATION,
     "Invalid token")                           reason="Session expired")
        │                                             │
 socket.reject(rejection, reason)          socket.close(close, reason)
        │                                             │
   ┌────┴─────────────┐                      ┌────────┴────────┐
 FastAPI-Adapter   WS-only-Adapter        FastAPI-Adapter   WS-only-Adapter
 → HTTP 401        → WS 1008              → WS 1008         → WS 1008
```

- `ConnectionRejected` bedeutet **immer**: der Handshake wurde nicht angenommen.
  Es gibt keinen HTTP-Status und keinen Close-Code am Typ.
- `ConnectionRejected` ist nur in Connect-Hooks gültig. Wird es nach `accept()`
  geraist (Methode oder Event), ist das ein Programmierfehler:
  - Methode: behandelt wie eine unerwartete Exception, **ohne** `error_mapper`:
    `internal_error`-Antwort, `logger.error("ConnectionRejected raised by %s
    after the connection was accepted; use RpcConnection.close() instead")`.
    Die Verbindung bleibt offen.
  - Event-Quelle: wie jede Exception einer Event-Quelle (§6.5), mit derselben
    Log-Meldung.
- `ConnectionRejected` ist **kein** `RpcError`.

### 5.4 `RpcConnectionClose`

```python
class RpcConnectionClose(StrEnum):
    NORMAL = "normal"
    SHUTDOWN = "shutdown"
    PROTOCOL_ERROR = "protocol_error"
    POLICY_VIOLATION = "policy_violation"
    MESSAGE_TOO_BIG = "message_too_big"
    INTERNAL_ERROR = "internal_error"
```

`MESSAGE_TOO_BIG` ergänzt die ursprünglichen fünf Gründe, weil
`RpcLimits.max_message_bytes` (§6.1) sonst semantisch falsch als
`POLICY_VIOLATION` gemeldet würde.

`RpcRejection` (§5.3) und `RpcConnectionClose` sind bewusst zwei Enums: das
eine beschreibt einen nicht angenommenen Handshake, das andere das Ende einer
angenommenen Verbindung. Keines der beiden enthält Transportcodes.

### 5.5 Port: `RpcSocket`

```python
class RpcDisconnect(Exception):
    """The peer closed or lost the connection."""

    def __init__(self, reason: str = "") -> None: ...


@dataclass(frozen=True, slots=True)
class RpcHandshake:
    path: str
    headers: Mapping[str, str] = MappingProxyType({})
    query_params: Mapping[str, str] = MappingProxyType({})
    path_params: Mapping[str, str] = MappingProxyType({})
    subprotocols: tuple[str, ...] = ()
    client: tuple[str, int] | None = None


class RpcSocket(Protocol):
    @property
    def handshake(self) -> RpcHandshake: ...

    async def accept(self, subprotocol: str | None = None) -> None: ...

    async def reject(self, rejection: RpcRejection, reason: str) -> None: ...

    async def receive(self) -> str | bytes: ...

    async def send(self, message: str) -> None: ...

    async def send_bytes(self, data: bytes) -> None: ...

    async def close(self, close: RpcConnectionClose, reason: str) -> None: ...
```

Vertrag, den jeder Adapter erfüllen muss:

- `receive()` liefert genau einen Frame. Text → `str`, Binär → `bytes`. Bei
  Verbindungsende raist es `RpcDisconnect`.
- `send()` sendet einen Text-Frame, `send_bytes()` einen Binärframe; ist die
  Verbindung weg, raisen beide `RpcDisconnect`. Beide warten, bis der Transport
  den Frame angenommen hat (Backpressure).
- `reject()` wird höchstens einmal und nur vor `accept()` aufgerufen. Der
  Adapter übersetzt `rejection` in die Mittel seines Transports (HTTP-Status,
  WebSocket-Close-Code, …).
- `close()` wird höchstens einmal und nur nach `accept()` aufgerufen. Der
  Adapter übersetzt `close` in seinen Transportcode.
- `handshake.headers` darf beliebig geschrieben sein; der Core normalisiert.
- `handshake.query_params`: bei Mehrfachwerten gewinnt der letzte.
- Der Core ruft `send()`/`send_bytes()` nie nebenläufig auf (ein Writer-Task).
- Der Core übergibt `reason` immer als String (ggf. `""`); Längenlimits des
  Transports setzt der Adapter durch.

### 5.6 WebSocket-Zuordnung: `pyrpckit.websocket`

Datei: `pyrpckit/websocket.py`. Framework-frei (nur Zahlen und Strings), aber
bewusst **nicht** im Core-Namespace `pyrpckit`, weil die Codes
WebSocket-spezifisch sind. Genutzt von `pyrpckit.fastapi`, `pyrpckit.testing`
nicht, und von eigenen Adaptern (`websockets`, aiohttp, …).

```python
CLOSE_CODES: Mapping[RpcConnectionClose, int] = MappingProxyType({
    RpcConnectionClose.NORMAL: 1000,
    RpcConnectionClose.SHUTDOWN: 1001,
    RpcConnectionClose.PROTOCOL_ERROR: 1002,
    RpcConnectionClose.POLICY_VIOLATION: 1008,
    RpcConnectionClose.MESSAGE_TOO_BIG: 1009,
    RpcConnectionClose.INTERNAL_ERROR: 1011,
})

# Für Transports, die einen Handshake nur per WebSocket-Close ablehnen können.
REJECTION_CLOSE_CODES: Mapping[RpcRejection, int] = MappingProxyType({
    RpcRejection.UNAUTHORIZED: 1008,
    RpcRejection.FORBIDDEN: 1008,
    RpcRejection.NOT_FOUND: 1008,
    RpcRejection.PROTOCOL_ERROR: 1002,
    RpcRejection.UNAVAILABLE: 1013,
    RpcRejection.INTERNAL_ERROR: 1011,
})

MAX_CLOSE_REASON_BYTES = 123


def close_reason(reason: str) -> str:
    """Truncate a close reason to 123 UTF-8 bytes without splitting a character."""
```

Beide Mappings sind vollständig (Test: jedes Enum-Mitglied hat einen Eintrag).

---

## 6. Laufzeit

Datei: `pyrpckit/runtime.py` (der Serving-Loop wandert aus `fastapi.py` hierher).

### 6.1 `RpcLimits`

```python
@dataclass(frozen=True, slots=True)
class RpcLimits:
    max_concurrency: int = 32
    max_queue_size: int = 128
    max_message_bytes: int | None = 1_048_576

    def __post_init__(self) -> None: ...   # ValueError bei < 1
```

`max_message_bytes` gilt für eingehende Frames (Länge in UTF-8-Bytes bzw.
`len(bytes)`); Überschreitung → Close mit `MESSAGE_TOO_BIG`.

### 6.2 Resolver

```python
type RpcResolverLike = RpcResolver | Callable[[type[Any]], Awaitable[Any] | Any]
```

- Objekte mit `resolve()` werden unverändert benutzt (inklusive optionalem
  `enter_connection` / `enter_scope`).
- Callables (z. B. `container.resolve`) werden in `FunctionResolver` gewrappt;
  ist das Ergebnis awaitable, wird es awaited.
- `None` → `EmptyResolver`.
- Normalisierung in `dependencies.as_resolver(value)`.

**Scopes pro Verbindung** (verbindlich):

```
base = ContextResolver(resolver, context ∪ {RpcConnection: connection})
hook scope       = call_scope(base)                    # nur falls ein Hook existiert
  └─ Hook läuft; Inject[T] löst hier auf
hook scope verlassen                                   # vor accept()
accept()
connection scope = connection_scope(resolver, context ∪ {RpcConnection} ∪ {T: hook_result})
  ├─ pro Methodenaufruf: route.resolver_scope(connection_resolver)   (wie heute)
  └─ Event-Quellen lösen im connection scope auf                   (wie heute)
connection scope verlassen                             # Resolver räumt auf (z. B. Dishka SESSION)
```

pyrpckit öffnet und schließt nur diese Scopes; alles, was darin an Ressourcen
entsteht und aufgeräumt wird, verantwortet der Resolver. Für Dishka landen
`RpcConnection` und das Hook-Ergebnis (z. B. `User`) im `SESSION`-Kontext und
sind damit auch in Dishka-Factories verfügbar (`from_context(provides=User,
scope=Scope.SESSION)`).

### 6.3 Ablauf von `endpoint.serve(socket, ...)`

```
1. rpc.freeze(); limits = limits or RpcLimits(); resolver = as_resolver(resolver)
2. connection = RpcConnection._create(endpoint, socket)
3. Falls endpoint.connect: im hook scope ausführen (5.2, 6.2). Ablehnung → reject, return.
4. (Hook-Scope ist jetzt verlassen.)
5. Subprotocol:
     endpoint.subprotocol is None           → accept(None)
     subprotocol in handshake.subprotocols  → accept(subprotocol)
     sonst                                  → reject(PROTOCOL_ERROR, "Unsupported subprotocol"), return
6. Verbindung intern als angenommen markieren (ab jetzt ist connection.close() erlaubt)
7. connection scope betreten; RpcServer für endpoint.protocol erzeugen
8. Tasks starten:
     writer        : liest outgoing-Queue, socket.send()
     reader        : socket.receive()-Schleife (6.5)
     event sources : je Event des Endpoints ein Task (wie heute)
9. Warten, bis eines eintritt:
     a) reader endet mit RpcDisconnect      → peer_closed = True
     b) close angefordert (6.4)
     c) Event-Quelle endet mit Exception    → close(INTERNAL_ERROR)
     d) serve-Task wird gecancelt           → close(SHUTDOWN), danach CancelledError weiterreichen
10. Shutdown (6.4), connection scope verlassen
```

Eine Event-Quelle, die normal endet, schließt die Verbindung **nicht**.

### 6.4 Schließen

Das Schließen fordern ausschließlich zwei Stellen an: `connection.close(close,
reason=...)` aus Anwendungscode oder die Runtime selbst (Limits, ungültige
Frames, fehlschlagende Event-Quellen, Shutdown). Verhalten:

1. Idempotent. Die **erste** Anforderung bestimmt Grund und Text.
2. `close()` blockiert nicht auf das Ende der Verbindung; es setzt den Zustand,
   weckt den Serve-Loop und kehrt zurück. Ein Handler, der `close()` aufruft,
   wird anschließend wie alle anderen laufenden Aufrufe gecancelt.
3. Der Reader stoppt; keine neuen Frames werden angenommen.
4. Alle laufenden Methoden-Tasks und Event-Quellen werden gecancelt und
   abgewartet.
5. Ausgehende Nachrichten, die bereits in der Queue liegen, werden bei
   `NORMAL` und `SHUTDOWN` noch gesendet und sonst verworfen.
6. `socket.close(close, reason)` — außer wenn der Peer bereits getrennt hat.
   `RpcDisconnect` beim Senden/Schließen wird ignoriert.
7. `connection.closed = True`.

Unerwartete Ausnahmen im Loop selbst (Bug) → `logger.exception`, Close mit
`INTERNAL_ERROR`.

### 6.5 Frames: Nachrichten- vs. Verbindungsfehler

| Situation | Kategorie | Reaktion |
|---|---|---|
| Frame > `max_message_bytes` | Verbindung | Close `MESSAGE_TOO_BIG` |
| Binärframe, kein gültiges UTF-8 | Verbindung | Close `PROTOCOL_ERROR`, reason `"Invalid RPC frame"` |
| Text/UTF-8, aber kein JSON | Nachricht | JSON-RPC `parse_error`, id `null` |
| JSON, aber kein gültiger Request | Nachricht | `invalid_request` |
| Unbekannte Methode | Nachricht | `method_not_found` |
| Ungültige Params | Nachricht | `invalid_params` mit Details |
| Handler raist `RpcError` | Nachricht | Fehlerantwort (§7) |
| Handler raist andere Exception | Nachricht | `error_mapper` → sonst `internal_error`; `logger.exception` |
| Handler raist `ConnectionRejected` | Nachricht (Programmierfehler) | `internal_error` ohne `error_mapper`, `logger.error` mit Hinweis auf `connection.close()` (§5.3) |
| Handler ruft `connection.close()` | Verbindung | Close mit übergebenem Grund |
| Event-Quelle raist Exception | Verbindung | `logger.exception`, Close `INTERNAL_ERROR` |
| Peer trennt | Verbindung | Shutdown ohne `socket.close()` |
| Serve-Task gecancelt | Verbindung | Close `SHUTDOWN` |

JSON-RPC-Notifications (Requests ohne `id`) erhalten nie eine Antwort, auch
nicht bei Fehlern — unverändert. Batches unverändert.

Logger: `logging.getLogger("pyrpckit")`.

### 6.6 Ablauf von `stream_endpoint.serve(socket, ...)`

```
1–6. wie §6.3: freeze, RpcConnection, Connect-Hook, Subprotocol, accept
7.   connection scope betreten (§6.2)
8.   handler scope = stream.resolver_scope(connection_resolver)   # wie ein Methodenaufruf,
                                                                 # aber für die ganze Stream-Dauer
9.   Inject-Parameter auflösen, Generator erzeugen
10.  Tasks starten:
       writer : async for frame in generator: prüfen, await socket.send_bytes(bytes(frame))
       reader : socket.receive()-Schleife
11.  Warten, bis eines eintritt:
       a) Generator endet normal              → close(NORMAL)
       b) Generator raist Exception           → logger.exception, close(INTERNAL_ERROR)
       c) Client sendet irgendeinen Frame     → close(PROTOCOL_ERROR, "Binary stream is server-to-client")
       d) reader endet mit RpcDisconnect      → peer_closed = True
       e) connection.close() angefordert      → close mit übergebenem Grund
       f) serve-Task wird gecancelt           → close(SHUTDOWN), danach CancelledError weiterreichen
12.  Shutdown (§6.4): writer canceln, generator.aclose() (finally-Blöcke laufen),
     handler scope und connection scope verlassen
```

- Es gibt **keine** Ausgangs-Queue: `send_bytes()` wird direkt aus dem
  Generator-Loop awaited, sodass ein langsamer Client den Generator bremst.
- Jeder geyieldete Wert muss `bytes`, `bytearray` oder `memoryview` sein,
  sonst `logger.error` und close(`INTERNAL_ERROR`).
- `ConnectionRejected` im Generator → wie §5.3 (Programmierfehler-Log),
  close(`INTERNAL_ERROR`).
- `RpcLimits.max_concurrency`, `max_queue_size` und `max_message_bytes`
  betreffen Stream-Endpoints nicht.

---

## 7. Fehler

Datei: `pyrpckit/errors.py`.

### 7.1 `RpcError`

```python
class RpcError(Exception):
    code: ClassVar[str]              # stabiler, maschinenlesbarer Identifier
    message: ClassVar[str]           # Default-Nachricht
    rpc_code: ClassVar[int] = RpcErrorCode.SERVER_ERROR   # -32000
    details_type: ClassVar[type[BaseModel] | None]         # aus `details`-Annotation

    details: Any                     # Instanz des Details-Modells oder None
    message: str                     # Instanz-Nachricht (überschattet ClassVar)

    def __init__(
        self,
        details: BaseModel | None = None,
        /,
        *,
        message: str | None = None,
        **fields: Any,
    ) -> None: ...
```

Deklaration:

```python
class ProjectNotFound(RpcModel):
    project_id: str


class ProjectNotFoundError(RpcError):
    message = "Project not found"
    details: ProjectNotFound


raise ProjectNotFoundError(project_id="p-1")                       # fields → Details-Modell
raise ProjectNotFoundError(ProjectNotFound(project_id="p-1"))      # explizit
raise ProjectNotFoundError(project_id="p-1", message="No such project: p-1")
```

Regeln (in `__init_subclass__`, Fehler → `ProtocolDefinitionError`):

- `code`: explizit oder abgeleitet: Klassenname ohne Suffix `Error`, dann
  snake_case (`ProjectNotFoundError` → `project_not_found`,
  `HTTPTimeoutError` → `http_timeout`). Muss `^[a-z][a-z0-9_]*$` matchen.
- `message`: explizit oder aus `code` abgeleitet
  (`project_not_found` → `"Project not found"`).
- `details`-Annotation (via `get_type_hints(cls)`): muss ein Pydantic-Modell
  sein; `details_type` wird gesetzt. Ohne Annotation `details_type = None`.
  Wird vererbt.
- `rpc_code`: `int`; entweder im Serverbereich `-32099..-32000` oder außerhalb
  des reservierten Bereichs `-32768..-32000`. Reservierte Standardcodes
  (`-32700`, `-32600..-32603`) sind nur für die eingebauten Fehler erlaubt.
- Das Details-Modell wird wie Params/Results mit `wire_annotation()` auf
  camelCase gebracht.
- Laufzeit (`__init__`):
  - `details_type is None` und (`details` oder `fields`) → `TypeError`.
  - `details_type` gesetzt: `fields` → `details_type(**fields)`; `details` und
    `fields` gleichzeitig → `TypeError`; weder noch → `TypeError`
    („ProjectNotFoundError requires details“).
  - `self.message = message or type(self).message`.
- Die bisherige Ad-hoc-Form `RpcError("msg", code=-32004)` entfällt. `RpcError`
  selbst ist abstrakt: direkte Instanziierung → `TypeError`.

### 7.2 Eingebaute Fehler

| Klasse | `code` | `rpc_code` | `details` |
|---|---|---|---|
| `RpcParseError` | `parse_error` | -32700 | – |
| `RpcInvalidRequestError` | `invalid_request` | -32600 | – |
| `RpcMethodNotFoundError` | `method_not_found` | -32601 | `RpcMethodNotFound(method: str)` |
| `RpcInvalidParamsError` | `invalid_params` | -32602 | `RpcInvalidParams(issues: list[RpcValidationIssue])` |
| `RpcInternalError` | `internal_error` | -32603 | – |

```python
class RpcValidationIssue(RpcModel):
    loc: list[str | int]     # ohne führendes "params"
    message: str
    type: str
```

`RpcInvalidParamsError.from_validation_error(error)` erzeugt Details aus
`error.errors(include_url=False)`; die Nachricht bleibt
`"Invalid params at params.name: Field required"` (erste Issue).

`RpcErrorCode` erhält `SERVER_ERROR = -32000`.

`declared_error(error)` prüft: `RpcError`-Unterklasse, nicht `RpcError` selbst,
nicht eine eingebaute Standardfehlerklasse (die sind implizit).

### 7.3 Wire-Format

```json
{
  "jsonrpc": "2.0",
  "id": 7,
  "error": {
    "code": -32000,
    "message": "Project not found",
    "data": {
      "code": "project_not_found",
      "details": { "projectId": "p-1" }
    }
  }
}
```

- `error.code` bleibt die JSON-RPC-Ganzzahl (`rpc_code`), damit generische
  JSON-RPC-Clients funktionieren.
- `error.data.code` ist **immer** gesetzt, auch für eingebaute Fehler.
  Clients unterscheiden Fehler ausschließlich darüber.
- `error.data.details` fehlt, wenn die Klasse kein Details-Modell hat.
- `RpcErrorData` erhält `data: RpcErrorPayload`; `RpcErrorPayload(code: str,
  details: Any = None)` serialisiert `details` mit dem Details-Typ und lässt
  `None` weg (`exclude_none` nur für dieses Feld).
- `RpcFailure.of(request_id, code, message)` → ersetzt durch
  `RpcFailure.from_error(request_id: RpcRequestId, error: RpcError)`.

### 7.4 Server-Verhalten

- `RpcServer._rpc_error()` unverändert in der Reihenfolge: `RpcError` →
  `error_mapper` → `ValidationError` → `RpcInternalError`.
- Ein geraister, aber nicht in `raises` deklarierter `RpcError` wird trotzdem
  normal serialisiert und auf `DEBUG` geloggt
  (`"undeclared RPC error %s raised by %s"`). Keine Erzwingung.
- `ConnectionRejected` aus einem Handler wird in `_rpc_error()` vor dem
  `error_mapper` behandelt: `logger.error(...)` (Text aus §5.3) und
  `RpcInternalError`. Identisch in `RpcServer.handle()` ohne Verbindung.

---

## 8. Contract

Datei: `pyrpckit/contract.py`.

### 8.1 `rpc.contract()`

```python
contract = rpc.contract(
    title="Session API",
    base_url="wss://api.example.com",
    variables={"session_id": ServerVariable(default="demo-session")},
)
```

`contract()` nimmt **kein** `binary_streams=`: Streams stammen aus
`rpc.stream()` (Prinzip 6).

- `base_url`: nicht leer; beginnt mit `ws://`, `wss://` oder einer Variablen
  (`"{origin}"`); kein abschließender `/` (wird sonst entfernt).
- Server-URL pro Endpoint: `base_url + endpoint.path`.
- OpenRPC-Server pro Endpoint in Registrierungsreihenfolge:

  ```json
  {
    "name": "control",
    "url": "wss://api.example.com/rpc",
    "summary": "<endpoint.summary, falls gesetzt>",
    "variables": { "...": "nur falls URL Variablen hat" },
    "x-rpckit-transport": {
      "type": "websocket",
      "messageEncoding": "json",
      "frameType": "text",
      "subprotocols": ["<endpoint.subprotocol, falls gesetzt>"]
    }
  }
  ```

- Variablen ohne Eintrag in `variables` bekommen wie heute
  `ServerVariable(default="{name}")`. Einträge in `variables`, die in keiner
  URL (Socket- und Stream-Endpoints) vorkommen → Fehler. `variables` gilt für alle URLs
  gleichermaßen; derselbe Name (`browser_id`) meint überall dasselbe.
- Jeder Stream-Endpoint wird in Registrierungsreihenfolge als Eintrag in
  `x-rpckit-binary-streams` gerendert, URL = `base_url + stream_endpoint.path`:

  ```json
  {
    "name": "browser.screencast.frames",
    "url": "wss://api.example.com/browsers/{browser_id}/screencast",
    "direction": "server-to-client",
    "contentType": "image/jpeg",
    "frameType": "binary",
    "tags": [{ "name": "..." }],
    "summary": "<Stream-Summary oder Endpoint-Summary, falls gesetzt>",
    "variables": { "browser_id": { "default": "{browser_id}" } },
    "subprotocols": ["<stream_endpoint.subprotocol, falls gesetzt>"]
  }
  ```

  `direction` ist immer `"server-to-client"` und bleibt im Dokument, damit
  weitere Richtungen später ohne Formatbruch hinzukommen können.
- Pfadvariablennamen bleiben im Contract exakt erhalten (`{session_id}`). Die
  Generatoren bilden sie idiomatisch ab (Python `session_id`, TypeScript
  `sessionId`); die Endpoint-Helper setzen weiterhin den Wire-Namen ins
  Template ein. Prüfen, dass `codegen/typescript.py::_identifier` camelCase
  erzeugt, sonst dort anpassen.
- `RpcContract` bleibt ein frozen dataclass mit `protocol`, `title`,
  `description`, `servers`, `binary_streams` (Tupel der gerenderten
  Stream-Dokumente als `MappingProxyType`, analog zu `servers`).
  `from_channels()` entfällt. `schema/openrpc.py::render_openrpc` bekommt die
  Stream-Dokumente aus `contract.binary_streams`.

### 8.2 OpenRPC-Fehlerobjekte

Pro Methode, für jede Klasse in `raises`:

```json
{
  "code": -32000,
  "message": "Project not found",
  "x-rpckit-code": "project_not_found",
  "x-rpckit-details-schema": { "$ref": "#/components/schemas/ProjectNotFound" }
}
```

Das bleibt gültiges OpenRPC 1.4.1 (`code` muss dort eine Ganzzahl sein), trägt
aber genau die Information aus dem Entwurf `{"code": "project_not_found",
"schema": "ProjectNotFound"}`.

- `x-rpckit-name` entfällt (ersetzt durch `x-rpckit-code`).
- `x-rpckit-data-schema` wird zu `x-rpckit-details-schema`.
- `schema/components.py` sammelt Details-Modelle aller deklarierten Fehler als
  Komponenten (inklusive transitiver Modelle).
- Eingebaute Fehler werden nicht pro Methode gelistet.

### 8.3 Codegen-Quelle

`schema/export.py::load_contract_source` akzeptiert nur noch `RpcContract`.

- `RpcService` → `ProtocolReferenceError("... is an RpcService; export rpc.contract(title=..., base_url=...) instead")`.
- `RpcChannel` → analog mit Hinweis auf `RpcService`.
- `render_contract(source)`: Channel-Zweig und `title`-Override-Pflicht entfallen;
  CLI-Hilfetexte (`codegen/cli.py`: „RpcChannel or RpcContract“) anpassen.

---

## 9. Codegen (Python und TypeScript)

Generatoren lesen weiterhin nur OpenRPC (AGENTS.md).

### 9.1 IR (`codegen/ir.py`)

```python
@dataclass(frozen=True, slots=True)
class ErrorDecl:
    rpc_code: int
    code: str                     # x-rpckit-code; fehlt es → UnsupportedSchemaError
    message: str
    details: TypeExpr | None      # x-rpckit-details-schema
```

Klassenname: `schema_name(code) + "Error"` (`project_not_found` →
`ProjectNotFoundError`). Fehler werden API-weit nach `code` dedupliziert.

### 9.2 Generierte Fehlerklassen

Python (`templates/python/errors.py.j2`, Runtime `runtime/errors.py.j2`):

```python
class RpcRemoteError(Exception):
    code: ClassVar[str] = ""

    def __init__(self, rpc_code: int, code: str, message: str, details: Any = None) -> None: ...


class ProjectNotFoundError(RpcRemoteError):
    code: ClassVar[str] = "project_not_found"
    details: ProjectNotFound


class RpcConnectionClosed(Exception):
    def __init__(self, code: int | None, reason: str) -> None: ...
```

TypeScript (`templates/typescript/errors.ts.j2`, `core.ts.j2`):

```typescript
export class RpcRemoteError extends Error {
  constructor(
    readonly rpcCode: number,
    readonly code: string,
    message: string,
    readonly details?: unknown,
  ) { super(message); this.name = "RpcRemoteError"; }
}

export class ProjectNotFoundError extends RpcRemoteError {
  static readonly code = "project_not_found";
  declare readonly details: ProjectNotFound;
}

export class RpcConnectionClosed extends Error {
  constructor(readonly code: number | undefined, readonly reason: string) { ... }
}
```

### 9.3 Client-Verhalten

- Beide Runtimes führen eine Registry `code → Fehlerklasse`. Eine Fehlerantwort
  wird zu der registrierten Klasse (Details in Python mit dem Modell validiert;
  scheitert die Validierung → Basis-`RpcRemoteError` mit Rohdaten). Unbekannter
  oder fehlender `data.code` → `RpcRemoteError` mit `code = ""`.
- Eingebaute Codes (`parse_error`, …) sind in beiden Runtimes als Klassen
  vorhanden (`RpcInvalidParamsError` etc.).
- Schließt der Server die Verbindung, werden alle offenen Requests mit
  `RpcConnectionClosed(code, reason)` abgelehnt (heute: generischer
  `Error("The WebSocket connection closed")` in `transport.ts.j2`).
- `examples/generated_clients` neu generieren.

### 9.4 Binary-Streams im Namespace-Baum

**IR** (`codegen/ir.py`):

```python
@dataclass(frozen=True, slots=True)
class BinaryStreamDecl:
    rpc_name: str                    # "browser.screencast.frames"
    operation_name: str              # "frames"
    path: tuple[str, ...]            # ("browser", "screencast")
    url: str
    content_type: str
    summary: str = ""
    description: str = ""
    tags: tuple[str, ...] = ()
    variables: tuple[ServerVariableDecl, ...] = ()
    subprotocols: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ApiNode:
    segment: str
    path: tuple[str, ...]
    operations: tuple[RouteDecl, ...] = ()
    streams: tuple[BinaryStreamDecl, ...] = ()
    children: tuple[ApiNode, ...] = ()
```

Streams werden wie Methoden anhand des Wire-Namens in den Baum einsortiert.
Kollisionen von Stream-, Methoden-, Event- und Namespace-Namen auf demselben
Knoten → `UnsupportedSchemaError`. Streams mit einer anderen `direction` als
`"server-to-client"` → `UnsupportedSchemaError` („only server-to-client binary
streams are supported“); `BinaryStreamDecl.direction` entfällt.

**Python**, generiert in der API-Klasse des Knotens:

```python
def frames(
    self,
    *,
    browser_id: str,                     # Pflicht, wenn der Contract-Default "{browser_id}" ist
    url: str | None = None,              # ersetzt das URL-Template; Variablen werden weiter eingesetzt
) -> BinaryStreamOpening: ...
```

Nutzung:

```python
async with client.browser.screencast.frames(browser_id="b-1") as frames:
    async for frame in frames:          # bytes
        ...

frames = await client.browser.screencast.frames(browser_id="b-1")
await frames.close()
```

**TypeScript**:

```typescript
frames(
  variables: { readonly browserId: string },
  options?: { readonly url?: string | URL; readonly incomingQueueSize?: number },
): Promise<BinaryStreamConnection>;
```

```typescript
await using frames = await client.browser.screencast.frames({ browserId: "b-1" });
for await (const frame of frames) {    // ArrayBuffer
  render(frame);
}
```

Variablen-Parameter: Variablen mit echtem Contract-Default (nicht `"{name}"`)
sind optional; Python nutzt snake_case, TypeScript camelCase (§8.1). Enum-
Variablen werden als Literal-Union typisiert (wie heute bei Servern).

**Runtime:**

- `BinaryStreamConnection` (Python und TS) hat `receive()`, asynchrone
  Iteration (endet sauber, wenn der Server mit `NORMAL` schließt), `close()`
  sowie `__aenter__`/`__aexit__` bzw. `Symbol.asyncDispose`. Es gibt kein
  `send()`. Schließt der Server mit einem anderen Grund, raisen `receive()` und
  die Iteration `RpcConnectionClosed(code, reason)` (§9.3).
- Python: `BinaryStreamOpening` ist awaitable (liefert die offene
  Verbindung) **und** ein async context manager (öffnet in `__aenter__`,
  schließt in `__aexit__`).
- Geöffnet wird über einen `BinaryStreamOpener` im Client-Core:
  Python `Callable[[BinaryStreamEndpoint], Awaitable[BinaryStreamTransport]]`,
  TypeScript `(endpoint: BinaryStreamEndpoint) => Promise<BinaryStreamTransport>`.
  Mit `with_transport = "websocket"` setzt der generierte `connect()` den
  gebündelten WebSocket-Opener als Default. Ohne Opener raist der Aufruf
  `RpcStreamsUnavailableError` („No binary stream opener configured; pass
  stream_opener=...“ bzw. `streamOpener`).
- Die Client-Optionen heißen `stream_opener` (Python) und `streamOpener` (TS).

**Module:** `media.py` / `media.ts` heißen jetzt `streams.py` / `streams.ts`
und enthalten nur noch Runtime-Typen (`BinaryStreamEndpoint`,
`BinaryStreamTransport` mit `receive()`/`close()`, `BinaryStreamConnection`,
`BinaryWebSocketStream`) plus die Metadaten-Konstante `binaryStreams` (TS) bzw.
`BINARY_STREAMS` (Python), jeweils nach Wire-Namen geschlüsselt. Die
bisherigen Endpoint-Helper pro Stream (`media.voice(...)`) und das
`BinaryStreamName`-Enum mit einfachen Namen entfallen; `BinaryStreamName`
enthält jetzt die vollständigen Wire-Namen.

---

## 10. FastAPI-Adapter

Datei: `pyrpckit/fastapi.py`. `serve()` entfällt.

### 10.1 `FastApiSocket`

```python
class FastApiSocket:
    def __init__(self, websocket: WebSocket) -> None: ...

    handshake: RpcHandshake                   # aus websocket.url.path, headers,
                                              # query_params, path_params,
                                              # scope["subprotocols"], client
    async def accept(self, subprotocol: str | None = None) -> None
    async def reject(self, rejection: RpcRejection, reason: str) -> None
    async def receive(self) -> str | bytes    # "websocket.disconnect" → RpcDisconnect(reason)
    async def send(self, message: str) -> None   # WebSocketDisconnect/RuntimeError → RpcDisconnect
    async def send_bytes(self, data: bytes) -> None   # dito
    async def close(self, close: RpcConnectionClose, reason: str) -> None
```

Der `WebSocket` liegt nur privat in `self._websocket`; es gibt keine
öffentliche Property.

HTTP-Zuordnung (privat in `pyrpckit/fastapi.py`, die einzige Stelle mit
HTTP-Status im Paket):

| `RpcRejection` | HTTP |
|---|---|
| `UNAUTHORIZED` | 401 |
| `FORBIDDEN` | 403 |
| `NOT_FOUND` | 404 |
| `PROTOCOL_ERROR` | 400 |
| `UNAVAILABLE` | 503 |
| `INTERNAL_ERROR` | 500 |

`reject()`: Unterstützt der Server die ASGI-Extension `websocket.http.response`
(`"websocket.http.response" in websocket.scope.get("extensions", {})`), dann
`await websocket.send_denial_response(Response(reason, status_code=<HTTP aus
der Tabelle>, media_type="text/plain"))`. Sonst
`await websocket.close(REJECTION_CLOSE_CODES[rejection], close_reason(reason))`
aus `pyrpckit.websocket`.

`close()`: `await websocket.close(CLOSE_CODES[close], close_reason(reason))`.

### 10.2 `create_router`

```python
def create_router(
    service: RpcService,
    *,
    resolver: RpcResolverLike | None = None,
    context: object | Mapping[type[Any], object] | None = None,
    error_mapper: RpcErrorMapper | None = None,
    limits: RpcLimits | None = None,
    prefix: str = "",
    dependencies: Sequence[params.Depends] | None = None,
) -> APIRouter: ...
```

- Friert die API ein und registriert pro Endpoint (Socket **und** Stream)
  `router.add_api_websocket_route(endpoint.path, handler, name=endpoint.name)`.
- `handler(websocket)` ruft `endpoint.serve(FastApiSocket(websocket), ...)`;
  `error_mapper` nur für `RpcEndpoint`.
- `prefix` und `dependencies` gehen an `APIRouter`. Ein Prefix ändert nicht den
  Contract; `base_url` muss ihn dann enthalten (README-Hinweis).

### 10.3 Manuelle Routen (FastAPI-`Depends`-Interop)

```python
control = rpc.endpoint("control")


@router.websocket(control.path)
async def control_socket(
    websocket: WebSocket,
    tenant: Tenant = Depends(current_tenant),
) -> None:
    await control.serve(FastApiSocket(websocket), context=tenant, resolver=resolver)
```

Das ist die bewusste Escape Hatch für volle FastAPI-Funktionalität.
`RpcService` integriert **kein** `Depends()` und kennt keine FastAPI-Typen.

`WebSocket` wird **nicht** mehr in RPC-Handler injiziert, und `RpcConnection`
gibt ihn nicht heraus (§5.1). Handler sehen nur die semantische Verbindung
(`Inject[RpcConnection]`). Wer den rohen `WebSocket` braucht, hat ihn in der
manuellen Route und übergibt daraus abgeleitete Werte als `context`.

---

## 11. Dishka

`pyrpckit/dishka.py` bleibt inhaltlich; `DishkaResolver` erfüllt weiterhin
`resolve` / `enter_connection` / `enter_scope`. Neu dokumentieren:

- Das Hook-Ergebnis und `RpcConnection` stehen im `SESSION`-Kontext
  (`from_context(provides=User, scope=Scope.SESSION)`).
- Der Connect-Hook löst im `REQUEST`-Scope vom Root auf, der vor `accept()`
  geschlossen wird (§6.2).
- Verbindungsgebundene Ressourcen mit Teardown sind `SESSION`-scoped Dishka-
  Provider, keine Hooks. Beispiel im README: eine DB-Verbindung pro Socket.

---

## 12. Testing-Hilfen

Datei: `pyrpckit/testing.py`. Keine Framework-Abhängigkeit. Der eigene
Testbestand nutzt sie für alle Runtime-Tests.

```python
class InMemorySocket:
    def __init__(
        self,
        path: str,
        *,
        headers: Mapping[str, str] | None = None,
        query_params: Mapping[str, str] | None = None,
        subprotocols: Iterable[str] = (),
    ) -> None: ...

    # Serverseite: implementiert RpcSocket
    # Clientseite:
    async def client_send(self, message: str | bytes) -> None
    async def client_receive(self) -> str | bytes    # nächster Serverframe
    async def client_disconnect(self, reason: str = "") -> None
    accepted: bool
    subprotocol: str | None
    rejection: tuple[RpcRejection, str] | None       # (rejection, reason)
    closed: tuple[RpcConnectionClose, str] | None


class RpcTestClient:
    """Serve one endpoint over an InMemorySocket and talk to it."""

    def __init__(
        self,
        service: RpcService,
        path: str,
        *,
        headers: Mapping[str, str] | None = None,
        subprotocols: Iterable[str] = (),
        resolver: RpcResolverLike | None = None,
        context: object | Mapping[type[Any], object] | None = None,
        error_mapper: RpcErrorMapper | None = None,
        limits: RpcLimits | None = None,
    ) -> None: ...

    async def __aenter__(self) -> "RpcTestClient": ...   # startet rpc.serve als Task
    async def __aexit__(self, *exc: object) -> None: ... # disconnect + Task abwarten

    socket: InMemorySocket
    async def request(self, method: str, params: Mapping[str, Any] | None = None) -> Any
    async def notify(self, method: str, params: Mapping[str, Any] | None = None) -> None
    async def next_notification(self) -> tuple[str, Any]
    async def next_frame(self) -> bytes              # Stream-Endpoints
```

- `request()` liefert `result` (JSON-dekodiert) oder raist
  `RpcTestError(rpc_code, code, message, details)`.
- `request()` auf eine abgelehnte oder geschlossene Verbindung raist
  `RpcTestConnectionClosed(rejection | closed)`.
- Notifications, die während eines `request()` eintreffen, werden gepuffert.
- `next_frame()` liefert den nächsten Binärframe eines Stream-Endpoints oder
  raist `RpcTestConnectionClosed`, sobald die Verbindung geschlossen ist.
  `request()` auf einem Stream-Endpoint → `TypeError`.

```python
async with RpcTestClient(rpc, "/rpc", headers={"Authorization": "t"}, resolver=r) as client:
    session = await client.request("control.create_session", {"projectId": "p-1"})
```

---

## 13. Öffentliche Exporte

`pyrpckit/__init__.py` (`__all__`, alphabetisch sortiert):

**Neu:** `ConnectionRejected`, `RpcConnection`, `RpcConnectionClose`,
`RpcDisconnect`, `RpcEndpoint`, `RpcHandshake`, `RpcLimits`, `RpcRejection`,
`RpcService`, `RpcSocket`, `RpcStreamEndpoint`, `RpcValidationIssue`.

Im Core-Namespace gibt es weder HTTP-Status noch WebSocket-Close-Codes.

**Bleibt:** `Inject`,
`ProtocolDefinitionError`, `RpcChannel`, `RpcCodec`, `RpcContract`, `RpcError`,
`RpcErrorCode`, `RpcErrorMapper`, `RpcFailure`, `RpcInternalError`,
`RpcInvalidParamsError`, `RpcInvalidRequestError`, `RpcMethodNotFoundError`,
`RpcModel`, `RpcNotification`, `RpcParseError`, `RpcRequestId`, `RpcResolver`,
`RpcResolverScope`, `RpcServer`, `RpcSuccess`, `ServerVariable`, `__version__`,
`call_scope`, `error_message`.

**Entfernt:** `RpcModule`.

**Nicht mehr exportiert** (nur auf dem unveröffentlichten Branch vorhanden,
daher kein Changelog-Eintrag): `BinaryStream`, `BinaryStreamDirection`.

Submodule: `pyrpckit.fastapi` exportiert `FastApiSocket`, `create_router`.
`pyrpckit.websocket` exportiert `CLOSE_CODES`, `REJECTION_CLOSE_CODES`,
`MAX_CLOSE_REASON_BYTES`, `close_reason`.
`pyrpckit.testing` exportiert `InMemorySocket`, `RpcTestClient`, `RpcTestError`,
`RpcTestConnectionClosed`. `pyrpckit.dishka` unverändert.

`__version__` und `pyproject.toml` bleiben `0.6.0`.

---

## 14. Breaking Changes und Changelog

Die Umsetzung **muss** den bestehenden Abschnitt `## 0.6.0` in `CHANGELOG.md`
vollständig durch folgenden Text ersetzen (Datum am Release-Tag setzen). Die
bisherigen 0.6.0-Einträge zu Binary-Streams sind darin aufgegangen. Jede
Abweichung der Umsetzung von dieser Spec wird im Text nachgezogen.

Maßstab für „Breaking“ ist das veröffentlichte 0.5.0. Änderungen gegenüber dem
unveröffentlichten Branch-Stand (z. B. `binary_streams=`, `BinaryStream.variables`,
das generierte `media`-Modul) sind **keine** Changelog-Einträge.

```markdown
## 0.6.0 - Unreleased

This release redesigns how RPC APIs are composed, served, and how errors reach
clients, and adds binary streams. It is intentionally incompatible with 0.5;
see "Migration" below.

### Breaking changes

- `RpcChannel` takes its name positionally (`RpcChannel("control")`) and its
  namespace now defaults to that name. Pass `namespace=""` for root-level
  methods.
- Removed `RpcModule`, `RpcChannel.include()`, and `RpcChannel(modules=...)`.
  Channels are endpoint-independent; combine them with
  `rpc.socket(path, *channels)`.
- Removed `RpcChannel(version=...)`; the protocol version is set on
  `RpcService(version=...)`.
- Renamed `errors=` to `raises=` on `@channel.method()`; channels accept
  `raises=` for errors shared by all their methods.
- Removed `pyrpckit.fastapi.serve()`. Use `create_router(rpc, ...)`, or a
  manual route calling `endpoint.serve(FastApiSocket(websocket), ...)`.
- The FastAPI `WebSocket` is no longer injectable into RPC handlers. Inject
  `RpcConnection` for path parameters, query parameters, headers, and the
  client address. Code that needs the raw `WebSocket` belongs in a manual
  FastAPI route.
- `serve()` options `max_concurrency` and `max_queue_size` moved into
  `RpcLimits`.
- Removed `RpcContract.from_channels()`. Build contracts with
  `rpc.contract(title=..., base_url=...)`; server URLs are derived from socket
  paths, and OpenRPC server names are endpoint names instead of channel names.
- Codegen sources must be `RpcContract` objects; `RpcChannel` references are
  rejected.
- `RpcError.code` is now a stable string identifier (derived from the class
  name, e.g. `project_not_found`). The JSON-RPC integer moved to `rpc_code` and
  defaults to `-32000`. Ad-hoc `RpcError("message", code=...)` instances and
  direct `RpcError` instantiation are no longer supported.
- Error responses always carry `error.data.code` and, for errors with a
  `details` model, `error.data.details`. Invalid params include structured
  validation issues.
- `RpcFailure.of()` was replaced by `RpcFailure.from_error()`.
- OpenRPC error objects use `x-rpckit-code` and `x-rpckit-details-schema`
  instead of `x-rpckit-name` and `x-rpckit-data-schema`.
- Generated Python and TypeScript clients raise error classes selected by
  `error.data.code`; `RpcRemoteError` exposes `rpc_code`/`rpcCode`, `code`, and
  `details`. Pending requests fail with `RpcConnectionClosed` when the server
  closes the socket.
- Sockets declaring a `subprotocol` reject clients that do not offer it
  (`RpcRejection.PROTOCOL_ERROR`, HTTP 400 in the FastAPI adapter) instead of
  accepting them.
- An event source that raises now closes its connection with close reason
  `internal_error` (WebSocket 1011) instead of failing silently.

### Added

- `RpcService` describes the socket topology: `rpc.socket(path, *channels,
  connect=..., subprotocol=...)` returns an `RpcEndpoint`.
- Transport port `RpcSocket` with `RpcHandshake` and `RpcDisconnect`;
  `rpc.serve(socket)` and `endpoint.serve(socket)` serve any implementation.
- A connect hook runs before a socket is accepted. It receives the
  `RpcConnection`, can inject dependencies, and provides its return value to
  `Inject[...]` in methods and events. Set one hook service-wide with
  `RpcService(connect=...)` or override it per socket; connection-scoped
  resources and teardown stay with your resolver.
- `raise ConnectionRejected(RpcRejection.UNAUTHORIZED, "...")` rejects a
  handshake from a connect hook. It is only valid there.
- `await connection.close(RpcConnectionClose.POLICY_VIOLATION, reason="...")`
  closes an accepted connection from a method or event source. It is only valid
  after the handshake was accepted.
- `RpcRejection` and `RpcConnectionClose` describe rejections and closes
  semantically. The FastAPI adapter maps rejections to HTTP status codes;
  `pyrpckit.websocket` provides WebSocket close-code mappings for custom
  adapters.
- `RpcLimits.max_message_bytes` closes connections that send oversized frames.
- Typed error details: annotate `details: Model` on an `RpcError` subclass and
  raise it with `raise MyError(field=value)`.
- `@channel.method`, `@channel.event`, and `@channel.stream` work without
  parentheses; event names and payload types are inferred from the function.
- Resolvers may be plain callables such as `container.resolve`.
- `pyrpckit.fastapi.FastApiSocket` and `create_router()`.
- `pyrpckit.testing` with `InMemorySocket` and `RpcTestClient`.
- Server-to-client binary streams: decorate an async generator yielding
  `bytes` with `@channel.stream()` and mount it with
  `rpc.stream(path, handler)`. Streams live in the channel's namespace
  (`browser.screencast.frames` beside `browser.screencast.start`), run on their
  own WebSocket with binary frames, and share connect hooks, subprotocols,
  close reasons, dependency injection, and `create_router()` registration with
  JSON-RPC sockets.
- Contracts describe binary streams with the `x-rpckit-binary-streams` OpenRPC
  extension.
- Generated Python and TypeScript clients expose binary streams as namespaced
  methods (`client.browser.screencast.frames(...)`) that support
  `async with` / `await using` and async iteration. Streams open through a
  configurable `stream_opener`/`streamOpener`; bundled WebSocket clients
  receive native binary frames and keep media traffic separate from the
  JSON-RPC connection.

### Migration

| 0.5 | 0.6 |
|---|---|
| `RpcChannel(name="c", namespace="c")` | `RpcChannel("c")` |
| `RpcModule(namespace="x")` + `channel.include(m)` | `RpcChannel("x")` + `rpc.socket(path, channel, x)` |
| `RpcChannel(version=2)` | `RpcService(version=2)` |
| `@c.method(errors=(E,))` | `@c.method(raises=[E])` |
| `await serve(channel, ws, resolver=r)` | `app.include_router(create_router(rpc, resolver=r))` |
| FastAPI `Depends` result as `context=` | connect hook, or `endpoint.serve(FastApiSocket(ws), context=...)` |
| `Inject[WebSocket]` | `Inject[RpcConnection]`; raw `WebSocket` only in a manual FastAPI route |
| `RpcContract.from_channels(channels=[...], server_urls={...})` | `rpc.contract(title=..., base_url=...)` |
| `class E(RpcError): code = -32004` | `class E(RpcError): rpc_code = -32004` (string `code` is derived) |
| `RpcError("msg", code=-32004)` | declare a subclass |
| `catch (e) { if (e.code === -32004) }` | `catch (e) { if (e instanceof ProjectNotFoundError) }` |
```

---

## 15. Dateien

| Datei | Aktion |
|---|---|
| `pyrpckit/app.py` | löschen, Inhalt → `channel.py` |
| `pyrpckit/router.py` | löschen, Helfer → `channel.py` |
| `pyrpckit/channel.py` | neu: `RpcChannel`, `RpcRoute`, Namens-Helfer |
| `pyrpckit/service.py` | neu: `RpcService`, `RpcEndpoint`, `RpcStreamEndpoint`, Pfad-Templates, kombiniertes Protokoll, Signaturanalyse des Connect-Hooks |
| `pyrpckit/connection.py` | neu: `RpcConnection`, `RpcHandshake`, `RpcSocket`, `RpcDisconnect`, `RpcConnectionClose`, `RpcRejection`, `ConnectionRejected`, `RpcLimits`, `_Headers` |
| `pyrpckit/websocket.py` | neu: `CLOSE_CODES`, `REJECTION_CLOSE_CODES`, `close_reason` (§5.6) |
| `pyrpckit/runtime.py` | neu: Serve-Loops für JSON-RPC- und Stream-Endpoints (§6.3, §6.6) |
| `pyrpckit/testing.py` | neu (§12) |
| `pyrpckit/errors.py` | `RpcError` neu (§7), Details-Modelle |
| `pyrpckit/envelopes.py` | `RpcErrorData.data`, `RpcErrorPayload`, `RpcFailure.from_error` |
| `pyrpckit/server.py` | `failure()` über `from_error`, `ConnectionRejected` als Programmierfehler (§7.4), Debug-Log |
| `pyrpckit/dependencies.py` | `RpcResolverLike`, `as_resolver`, `FunctionResolver` |
| `pyrpckit/protocol.py` | Event-Payload-Inferenz; `errors` → `raises` im Definitionsobjekt umbenennen; `RpcStreamDefinition`, `RpcProtocol.streams` |
| `pyrpckit/contract.py` | `from_channels` und `BinaryStream` entfernen, Server- und Stream-Dokumente nach `rpc.contract()` |
| `pyrpckit/schema/openrpc.py` | Fehlerobjekte (§8.2) |
| `pyrpckit/schema/components.py` | Details-Modelle als Komponenten |
| `pyrpckit/schema/export.py` | nur `RpcContract` (§8.3) |
| `pyrpckit/codegen/cli.py` | Hilfetexte |
| `pyrpckit/codegen/ir.py` + Templates | §9; `media.*.j2` → `streams.*.j2`, Stream-Methoden in `api.*.j2` / `macros.*.j2` |
| `pyrpckit/fastapi.py` | §10 |
| `pyrpckit/__init__.py` | §13 |
| `AGENTS.md` | „decorated handler classes“ → Channels/API; Absatz zum `RpcSocket`-Port; Dateiliste |
| `README.md` | komplett auf die neue API umschreiben (§16) |
| `examples/*.py` | auf neue API umstellen; `examples/generated_clients` regenerieren |
| `CHANGELOG.md` | Abschnitt `## 0.6.0` durch §14 ersetzen |

---

## 16. README und Beispiele

README-Gliederung beibehalten, Inhalte ersetzen:

1. **Your first API** — Channel, Methode, `RpcService`, `rpc.socket`,
   `create_router`.
2. **Sockets and channels** — mehrere Channels auf einem Socket, mehrere
   Sockets, Namespaces, Endpoint-Namen.
3. **Connections** — Connect-Hook als Context-Builder, `ConnectionRejected` +
   `RpcRejection` (nur Handshake) vs. `connection.close()` (nur nach Accept),
   Verweis auf den Resolver für verbindungsgebundene Ressourcen,
   `RpcConnection.close()`, Close-Gründe-Tabelle (§5.4) und die Tabelle
   Nachrichten- vs. Verbindungsfehler (§6.5, gekürzt).
4. **Send typed events** — Inferenz von Name und Payload.
5. **Errors** — `raises`, Details, Wire-Format, generierter Client mit
   `instanceof`.
6. **Binary streams** — `@channel.stream()` + `rpc.stream()` (Browser-Screencast
   aus §1.5), Unterschied zu JSON-RPC-Methoden, gemeinsamer Connect-Hook,
   Backpressure, nur Server → Client, generierter Client mit `async with` /
   `for await`.
7. **Contract and clients** — `rpc.contract()`, `base_url` mit Prefix,
   `rpcgen.toml`.
8. **Custom transports** — `RpcSocket` implementieren (vollständiges
   Beispiel mit der `websockets`-Bibliothek, ~40 Zeilen), `rpc.serve()`.
9. **FastAPI and Dishka** — `create_router`, manuelle Route mit `Depends`,
   Dishka-Kontext.
10. **Testing** — `RpcTestClient`.

`examples/basic.py` nutzt `RpcTestClient` statt `channel.server()`;
`examples/features.py` zeigt zwei Channels auf einem Socket;
`examples/notifications.py` zeigt Events ohne `payload=`; das Beispiel für
`examples/generated_clients` deklariert und mountet seine Streams per
`@channel.stream()` / `rpc.stream()`. `tests/test_examples.py`
muss weiter grün sein.

---

## 17. Tests

Tests spiegeln die Paketstruktur (AGENTS.md). Mindestumfang:

**`tests/test_channel.py`**
- Name positional, Namespace-Default = Name, `namespace=""`.
- `@method` mit und ohne Klammern; Nicht-Funktion → Fehler.
- `raises`/`tags` Merge und Deduplizierung.
- Event-Name- und Payload-Inferenz; widersprüchliches `payload=` → Fehler.
- Registrierung nach `freeze()` → Fehler.
- `@stream()`: mit/ohne Klammern, Wire-Name mit Namespace, Tags-Merge,
  Summary aus Docstring, Namenskollision mit Methode; falsche Return-Annotation,
  Pydantic-Parameter und `AsyncIterator[bytes]`-Eingang → Fehler.

**`tests/test_service.py`**
- Pfadvalidierung (jede Regel aus §4.2 einzeln).
- Name-Ableitung und Fehler bei fehlendem statischen Segment.
- Channel doppelt gemountet, doppelte Endpoint-Namen/Pfade.
- `freeze()`: doppelte Methodennamen über Channels, doppelte Error-Codes,
  `socket()` nach Freeze.
- `match()`: statisch vor variabel, URL-Dekodierung, Sockets und Streams
  gemeinsam.
- `stream()`: undekorierte Funktion → Fehler, Stream doppelt gemountet,
  Pfad-/Namenskollision mit Socket, Channel nur mit Streams, nicht gemounteter
  Stream fehlt in Protokoll und Router.

**`tests/test_connect.py`**
- Signaturregeln: sync und async generator → Fehler; unbekannter Parameter;
  fehlende, `None`-, Union- und `RpcConnection`-Return-Annotation.
- Socket-Hook ersetzt Service-Hook; ohne beide läuft kein Hook.
- Hook-Ergebnis ist in Methoden und Events per `Inject[T]` verfügbar;
  `None`-Return legt nichts ab.
- `Inject[T]` im Hook löst über den Resolver in einem Call-Scope auf, der vor
  `accept()` verlassen ist.
- Kollision von Hook-Typ und statischem `context` → `ValueError`.

**`tests/test_runtime.py`** (über `InMemorySocket` / `RpcTestClient`)
- Happy Path: Request/Response, Notification ohne Antwort, Batch.
- `ConnectionRejected` im Hook → `rejection == (RpcRejection.UNAUTHORIZED, ...)`, kein `accept`.
- Default-Reason wird aus dem Rejection-Enum abgeleitet.
- Unerwartete Exception im Hook → `(RpcRejection.INTERNAL_ERROR, "Internal error")`.
- `connection.close()` im Hook → `RuntimeError` → `INTERNAL_ERROR`-Rejection.
- Subprotocol: akzeptiert / `RpcRejection.PROTOCOL_ERROR`.
- `ConnectionRejected` im Handler → `internal_error`-Antwort, `error_mapper`
  nicht aufgerufen, Error-Log, Verbindung bleibt offen.
- `ConnectionRejected` in Event-Quelle → Close `INTERNAL_ERROR`.
- `RpcConnection` hat keine öffentlichen Attribute außer denen aus §5.1
  (Test über `dir()` ohne `_`-Präfix).
- `connection.close(PROTOCOL_ERROR)` aus Handler; laufende andere Requests
  werden gecancelt; Idempotenz (zweiter Grund wird ignoriert).
- Queue-Flush bei `NORMAL`, Verwerfen bei `INTERNAL_ERROR`.
- Zu großer Frame → `MESSAGE_TOO_BIG`; ungültiges UTF-8 → `PROTOCOL_ERROR`;
  kaputtes JSON → `parse_error`-Antwort, Verbindung bleibt offen.
- Event-Quelle raist → `INTERNAL_ERROR`; endet normal → Verbindung bleibt offen.
- Cancel des Serve-Tasks → `SHUTDOWN`.
- `max_concurrency` begrenzt parallele Handler.
- Resolver als Callable (sync und async).
- `rpc.serve()` wählt Endpoint per Pfad; unbekannter Pfad → `reject(RpcRejection.NOT_FOUND, "Not found")`.

**`tests/test_runtime_streams.py`**
- Frames kommen in Reihenfolge als Binärframes an; Generator-Ende → `NORMAL`.
- Connect-Hook und Subprotocol gelten wie bei Sockets; Hook-Ergebnis per
  `Inject[T]` im Stream-Handler.
- Client sendet Frame → `PROTOCOL_ERROR`; Generator raist → `INTERNAL_ERROR`;
  Nicht-bytes geyieldet → `INTERNAL_ERROR`.
- Disconnect des Clients → Generator wird mit `aclose()` beendet (finally läuft).
- `connection.close(POLICY_VIOLATION)` im Generator; Cancel → `SHUTDOWN`.
- Backpressure: ein blockierendes `send_bytes()` pausiert den Generator.
- Resolver-Scope des Channels umschließt die gesamte Stream-Dauer.

**`tests/test_errors.py`**
- Code- und Message-Ableitung (inkl. Akronyme), explizite Overrides,
  ungültiger Code, `rpc_code`-Bereiche.
- `details`-Annotation: Nicht-Modell → Fehler; Konstruktion per Feldern und
  Instanz; fehlende/überflüssige Details → `TypeError`.
- Wire-Format exakt wie §7.3 (camelCase in `details`, `details` fehlt ohne
  Modell); `invalid_params` mit Issues; undeclared Error wird serialisiert.

**`tests/test_contract.py` / `tests/test_schema*.py`**
- Server pro Endpoint, URL aus `base_url + path`, Subprotocol, Summary.
- Variablen-Defaults und unbenutzte Variablen.
- Gemountete Streams erscheinen mit Wire-Name, `base_url + path`,
  `direction: "server-to-client"`, Subprotocol; nicht gemountete nicht;
  gemeinsame Variablen mit Sockets.
- Blatt-/Namespace-Kollision (`browser.screencast` vs.
  `browser.screencast.start`) → Fehler in `freeze()`.
- `contract()` akzeptiert kein `binary_streams=`.
- Fehlerobjekte mit `x-rpckit-code` / `x-rpckit-details-schema`, Details-Modell
  in `components`.
- `load_contract_source` lehnt `RpcService` und `RpcChannel` mit Hinweis ab.

**`tests/test_fastapi.py`** (neu, mit `fastapi.testclient.TestClient`)
- `create_router`: Pfadparameter kommen in `connection.path_params` an.
- Jede `RpcRejection` ergibt den HTTP-Status aus §10.1 (Denial-Response) bzw.
  `WebSocketDisconnect` mit dem Code aus `REJECTION_CLOSE_CODES`, je nach
  Server-Support.
- Close nach Accept liefert den Code aus `CLOSE_CODES`.
- `FastApiSocket` hat keine öffentliche `websocket`-Property.
- Manuelle Route mit `Depends` → `context`.
- `create_router` registriert Stream-Endpoints; Frames kommen als Binärframes an.

**`tests/test_websocket.py`**
- `CLOSE_CODES` und `REJECTION_CLOSE_CODES` decken jedes Enum-Mitglied ab.
- `close_reason()` kürzt auf 123 Bytes, ohne ein Multibyte-Zeichen zu teilen.

**`tests/test_dishka.py`**
- Hook-Ergebnis im `SESSION`-Kontext verfügbar; `SESSION`-Provider mit
  Finalizer wird nach Disconnect aufgeräumt.

**`tests/codegen/*`**
- IR liest `x-rpckit-code`/`x-rpckit-details-schema`; fehlender Code → Fehler.
- Python/TS-Templates erzeugen String-Codes und Details-Typen; Roundtrip-Test:
  Server raist `ProjectNotFoundError`, generierter Python-Client raist die
  generierte Klasse mit validierten Details.
- Verbindungs-Close → `RpcConnectionClosed`.
- Streams im Namespace-Baum (Python und TS), Variablen-Parameter,
  `url`-Override, fehlender Opener → `RpcStreamsUnavailableError`,
  `async with` / `await using`, Iteration endet bei `NORMAL`, anderer Close →
  `RpcConnectionClosed`; IR lehnt andere Richtungen ab.

**`tests/test_public_api.py`**
- `RpcModule` zur Liste entfernter Symbole hinzufügen; `RpcRouter` bleibt
  entfernt; neue Exporte vorhanden.

---

## 18. Bewusst nicht enthalten

- Methoden, die Streams zurückgeben (`subscribe() -> EventStream`). Events
  bleiben `@channel.event`-Generatoren pro Verbindung; parametrisierte
  Subscriptions sind eine eigene Spec.
- Ein Channel auf mehreren Sockets.
- Mehrere Connect-Hooks pro Socket, Hook-Ketten, Hooks, die Ergebnisse anderer
  Hooks injizieren, async-generator-Hooks und Hook-Teardown. Verbindungsgebundene
  Ressourcen verwaltet der Resolver.
- `Depends()`-Integration in `RpcService` oder `create_router`. Volle
  FastAPI-Funktionalität gibt es ausschließlich über manuelle Routen (§10.3).
- Zugriff auf Socket oder Framework-Objekte über `RpcConnection`.
- Erzwingung, dass nur deklarierte Fehler geraist werden.
- Client→Server- und bidirektionale Binary-Streams. Contract-Format
  (`direction`) und Port (`receive()` liefert `bytes`) sind dafür vorbereitet.
- Externe Media-Server: Streams, die pyrpckit nicht selbst servt (absolute
  URLs, reine Contract-Deklarationen).
- Mehrere Streams auf einem Socket oder Streams auf JSON-RPC-Sockets.
- Ressourcen-Semantik im Client (`cast = await ...start(); cast.frames()`),
  gebundene Clients (`client.browser(browser_id)`) und automatische
  Variablen-Übernahme aus Methoden-Ergebnissen.
- Mehrere Server-Umgebungen pro Contract (`base_url` ist genau eine URL;
  Umgebungen über eine Variable wie `"{origin}"` modellieren).

---

## 19. Reihenfolge der Umsetzung

1. `errors.py`, `envelopes.py`, `server.py` (§7) inkl. Tests — unabhängig vom Rest.
2. `channel.py` (§3), `router.py`/`app.py` entfernen, bestehende Tests umstellen.
3. `connection.py`, `service.py` (§4, §5).
4. `runtime.py`, `testing.py` (§6, §12), Runtime-Tests; danach Stream-Endpoints
   (§3.3, §6.6).
5. `fastapi.py`, `dishka.py`-Tests (§10, §11).
6. `contract.py`, `schema/*` (§8).
7. Codegen IR, Templates, Runtimes, `examples/generated_clients` (§9).
8. `__init__.py`, Version, README, Beispiele, AGENTS.md, CHANGELOG (§13–§16).

Abnahme: `uv run ruff check .`, `uv run ruff format --check .`, `uv run pytest`
grün; `pyrpckit generate --config ... --check` für die Beispiele grün; keine
Referenz auf `RpcModule`, `from_channels`, `errors=` (als Methodenparameter),
`x-rpckit-name`, `x-rpckit-data-schema` oder `fastapi.serve` mehr im Repository
außerhalb von `CHANGELOG.md`. Im Paket `pyrpckit/` gibt es keine HTTP-Status
außerhalb von `fastapi.py` und keine WebSocket-Close-Codes außerhalb von
`websocket.py` und `fastapi.py` (Codegen-Templates ausgenommen).
