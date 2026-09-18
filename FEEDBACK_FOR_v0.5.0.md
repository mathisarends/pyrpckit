# pyrpckit 0.5.0 – Feedback aus der Cara-Migration

Kontext: Migration von 0.4 auf 0.5 im Cara Gateway (FastAPI + Dishka, ein
JSON-RPC-WebSocket `/v1/gateway`, 13 Methoden, 2 Event-Streams, generierter
Python-Client für CLI und Voice-Client). Stand 2026-09-18.

Kurzfazit: 0.5 ist ein klarer Schritt nach vorn. `RpcService` +
Connect-Hook + Library-Runtime haben rund 200 Zeilen Cara-eigenen
Verbindungscode (`GatewayConnection`, `EventForwarder`) ersetzt, und typisierte
Fehler im Client machen Magic-Numbers überflüssig. Es gibt aber einen echten Bug
(siehe 1) und einige Ergonomie-Kanten beim Einbetten in eine bestehende
FastAPI/Dishka-App.

### Nachtrag: Entscheidung für 0.6

Der Connect-Hook ist als Modell richtig, lohnt sich in Cara aber erst, wenn eine
authentifizierte Identität per `Inject[...]` in RPC-Handler gelangen oder Auth
Teil des RPC-Contracts werden soll. Beides ist heute nicht der Fall. Deshalb
sollte 0.6 den Hook und die zugehörige Auth-Abstraktion wieder entfernen.

Cara kann den bestehenden Authenticator in seiner eigenen FastAPI-Route direkt
vor `serve()` aufrufen. Das ist eine Zeile, entspricht exakt dem bereits
verwendeten Media-Socket und hält beide Sockets auf demselben Auth-Weg. Dadurch
entfallen `authenticate_rpc_connection`, dessen pyrpckit-Import, `connect=` am
Socket und der Auth-Header im `RpcTestClient`.

Die bewussten Folgen sind überschaubar:

- Auth läuft nicht im `RpcTestClient`; die Auth-Tests nutzen ohnehin den echten
  FastAPI-Socket.
- Fehler erscheinen wieder als WebSocket-Close 1008 mit `Unauthorized` statt
  als HTTP 401. Für Clients sind beide ein fehlgeschlagener Handshake.
- Eine Identität kann nicht aus einem Connect-Hook in Handler injiziert werden.
  Falls Cara das später benötigt, kann die Funktion gezielt neu bewertet werden.

### Umsetzungsstand für 0.6

Das Feedback wurde vollständig gegen die Library geprüft. Für 0.6 sind folgende
Punkte umgesetzt:

- Der Starlette-`TestClient`-Disconnect verliert keinen `CancelledError` mehr;
  ein Regressionstest öffnet und schließt 200 echte FastAPI-WebSockets.
- `RpcService` hält gemeinsame Defaults für `error_mapper` und `limits`, die ein
  Endpoint überschreiben kann. `serve()`, `create_router()` und
  `RpcTestClient` verwenden dadurch dieselbe Konfiguration.
- `create_router(resolver_factory=...)` löst Resolver pro Verbindung auf.
  `dishka_router()` liest den APP-Container aus `app.state` und
  `DishkaResolver` erklärt einen versehentlich übergebenen SESSION-Container.
- `socket(..., channels=...)` nimmt eine benannte Sequenz.
- `channel.child()` bildet verschachtelte Namespaces und vererbt `raises` sowie
  den Resolver-Scope. Alternativ kann der Channel-Name aus `namespace=` folgen;
  Fehlermeldungen für gepunktete Operationen verweisen auf Kind-Channels.
- Fehlercodes entfernen sowohl `Error` als auch `RpcError` als Suffix.
  `RpcInvalidParamsError` erlaubt leere Issues und ein versehentlich positional
  übergebener Meldungstext verweist auf `message=`. Die Wire- und
  `RpcRemoteError`-Änderungen sind im Changelog hervorgehoben.
- Ein service-gebundener `observer=` beobachtet Request-Start, Request-Ende und
  Connection-Close ohne modulweiten Zustand. `RpcConnection` stellt Close-Code
  und Grund bereit; die nebenläufige Verarbeitung ist dokumentiert.
- Generierte Python-Clients akzeptieren `headers=`, ihre Connection ist direkt
  awaitbar und Single-Server-Clients verbinden standardmäßig eager. Der
  pfadbasierte Servername ist im Changelog genannt. Generierter Python-Code
  wird bereits in der Testsuite mit `ruff check` und `ruff format --check`
  geprüft; ein externer Post-Process-Hook ist daher nicht nötig.
- Contract-Basis-URLs akzeptieren HTTP(S) und werden nach WS(S) übersetzt.
  `RpcContract.to_openrpc()` erhält auch Binary-Stream-Erweiterungen, und das
  JSON-Rendering bewahrt Unicode.

Bewusst zurückgestellt sind Client→Server- und bidirektionale Binary-Streams.
Sie benötigen vor einer öffentlichen API noch Entscheidungen zu Framing,
Backpressure, Ownership und Codegen in beiden Zielsprachen. Eine halbe
`direction=`-API würde den Cara-Media-Socket nicht sicher ersetzen. Bis dieses
Protokoll separat entworfen ist, bleibt der spezialisierte bidirektionale Socket
die passendere Lösung.

---

## 1. Bug: `CancelledError` beim Verbindungsende unter Starlettes `TestClient`

**Schwere: hoch.** Betrifft jeden, der pyrpckit + FastAPI mit dem Standard-TestClient testet.

Minimal-Reproduktion ohne Cara-Code:

```python
ch = rpc.RpcChannel("demo")

@ch.method
async def echo(params: P) -> R: ...

service = rpc.RpcService()
service.socket("/ws", ch)
app = FastAPI()
app.include_router(create_router(service))

with TestClient(app) as client:
    for i in range(200):
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"jsonrpc": "2.0", "id": 1, "method": "demo.echo", "params": {"x": i}})
            ws.receive_json()
```

Ergebnis: **111 von 200** Sessions enden beim Verlassen des `with`-Blocks mit
`concurrent.futures.CancelledError`. Mit einem Event-Source auf dem Channel sind
es 76 von 200.

Analyse:
- Starlettes `WebSocketTestSession.__exit__` schickt `websocket.disconnect` und
  cancelt direkt danach den anyio-`CancelScope` der App.
- `serve_endpoint` erkennt den Disconnect im *Reader-Task*, setzt ein Event, und
  der Haupt-Task braucht dann noch mehrere Loop-Runden, um aufzuräumen:
  Hintergrund-Tasks canceln und in `runtime.py:190` per
  `gather(..., return_exceptions=True)` einsammeln, danach die Scopes schließen.
- In diesem Fenster trifft der anyio-Cancel. Instrumentiert: Der Abbruch passiert
  in `serve_endpoint:190`, und in einem Teil der Fälle entkommt der
  `CancelledError` dem Cancel-Scope des Hosts.
- Der alte, handgeschriebene Loop (Disconnect direkt im App-Task behandelt,
  sofort return) war davon nicht betroffen.

Vorschläge:
- Disconnect im Haupt-Task behandeln bzw. nach `RpcDisconnect` ohne weitere
  ungeschützte Awaits zurückkehren.
- Cleanup nach Cancellation nicht mehr awaiten (oder shielden).
- Einen Regressionstest mit `fastapi.testclient.TestClient` in einer Schleife
  in die Library-Suite aufnehmen.

Workaround in Cara: RPC-Verhaltenstests laufen über `RpcTestClient` im Portal
der App (`client.portal.call(...)`), nur die Auth-Denial-Tests über den echten
Socket.

## 2. Einbettung in FastAPI + Dishka

### Ist-Zustand in Cara

Drei Stellen müssen dieselbe Serve-Konfiguration kennen, und keine davon kann
`create_router()` nutzen:

```python
# presentation/rpc/service.py
gateway_service = RpcService()
gateway_endpoint = gateway_service.socket(
    "/v1/gateway", *channels, connect=authenticate_rpc_connection
)

# presentation/websocket/router.py – eigene Route, weil create_router() den
# Resolver schon beim Import braucht, der Dishka-Container aber erst mit der App
# entsteht (fastapi-canon baut ihn in Composition.apply()).
@gateway_router.websocket(gateway_endpoint.path, name="gateway_socket")
async def gateway_socket(websocket: WebSocket) -> None:
    # The app container, not the socket's: the resolver opens the session scope.
    await gateway_endpoint.serve(
        FastApiSocket(websocket),
        resolver=DishkaResolver(websocket.app.state.dishka_container),
        error_mapper=gateway_error_mapper,
    )

# tests/presentation/websocket/test_router.py – dieselbe Konfiguration noch einmal
def _gateway(app: FastAPI) -> RpcTestClient:
    return RpcTestClient(
        gateway_service,
        "/v1/gateway",
        headers=_AUTH_HEADERS,
        resolver=DishkaResolver(app.state.dishka_container),
        error_mapper=gateway_error_mapper,
    )
```

Die drei Probleme dahinter:

- **2a. Resolver zur Definitionszeit:** `create_router(service, resolver=...)`
  braucht einen fertigen Resolver. Frameworks, die den Container erst beim
  App-Aufbau erzeugen (Dishka, fastapi-canon), passen nicht dazu.
- **2b. Scope-Falle bei Dishka:** Dishkas FastAPI-Middleware öffnet pro WebSocket
  schon einen SESSION-Container (`websocket.state.dishka_container`).
  `DishkaResolver.enter_connection` öffnet selbst noch einmal SESSION und braucht
  deshalb den APP-Container aus `app.state`. Wer intuitiv den Socket-Container
  nimmt, bekommt einen Scope-Fehler.
  - Einschränkung: Einen bestehenden SESSION-Container einfach zu übernehmen geht
    nicht, weil der Dishka-Kontext beim Öffnen feststeht und `RpcConnection`
    dann nicht mehr hineinkommt. Die richtige Lösung ist also nicht „bestehenden
    Scope übernehmen“, sondern „die Integration kümmert sich darum“.
- **2c. Serve-Konfiguration pro Aufruf:** `error_mapper` und `limits` gehören
  fachlich zum Endpoint (genau wie `connect`), müssen aber bei jedem `serve()`,
  in `create_router()` und im `RpcTestClient` erneut übergeben werden.

### Vorschlag

**(1) Serve-Konfiguration am Service/Endpoint, pro Endpoint überschreibbar:**

```python
gateway_service = RpcService(
    error_mapper=gateway_error_mapper,
    limits=RpcLimits(max_concurrency=16),
)
gateway_endpoint = gateway_service.socket(
    "/v1/gateway",
    channels=GATEWAY_CHANNELS,
    connect=authenticate_rpc_connection,
    # error_mapper=..., limits=...   # optional: überschreibt den Service-Default
)
```

`serve()`, `create_router()` und `RpcTestClient` behalten die Parameter nur als
optionale Überschreibung. Für Tests heißt das: Sie nutzen automatisch dieselbe
Fehlerabbildung wie Produktion.

**(2) Resolver als Factory, die pro Verbindung aufgelöst wird:**

```python
type RpcResolverFactory = Callable[[RpcHandshake], RpcResolverLike]

def create_router(
    service: RpcService,
    *,
    resolver: RpcResolverLike | RpcResolverFactory | None = None,
    ...
) -> APIRouter: ...
```

Die Factory bekommt den Handshake. Für FastAPI braucht sie allerdings Zugriff auf
die App. Deshalb eher:

**(3) Eine Dishka-FastAPI-Integration in pyrpckit, die genau das kapselt:**

```python
# pyrpckit/dishka.py
def dishka_router(service: RpcService) -> APIRouter:
    """Serve every endpoint with the app's Dishka container.

    Opens one SESSION scope per connection with ``RpcConnection`` in its
    context and one REQUEST scope per call; the app container is read from
    ``app.state.dishka_container`` at connect time.
    """
```

Damit schrumpft Cara auf:

```python
# presentation/rpc/service.py
gateway_service = RpcService(error_mapper=gateway_error_mapper)
gateway_endpoint = gateway_service.socket(
    "/v1/gateway",
    channels=GATEWAY_CHANNELS,
    connect=authenticate_rpc_connection,
)

# platform_feature.py
platform_feature = Feature(
    name="platform",
    routers=[dishka_router(gateway_service)],
    providers=[..., GatewayProvider, FastapiProvider],
)

# tests
def _gateway(app: FastAPI) -> RpcTestClient:
    return RpcTestClient(
        gateway_service,
        "/v1/gateway",
        headers=_AUTH_HEADERS,
        resolver=DishkaResolver(app.state.dishka_container),
    )
```

- Die eigene Route verschwindet, ebenso der erklärende Kommentar zum
  APP-Container.
- Der Test dupliziert nur noch den Resolver, und das ist genau der Teil, der sich
  zwischen Test und Produktion legitim unterscheiden darf.
- Nebeneffekt: Weil die Integration die Route selbst registriert, kann sie für
  RPC-Pfade den doppelten SESSION-Scope der Dishka-Middleware vermeiden (2b).
  Das sollte zumindest dokumentiert sein.

**(4) Zusätzlich: `DishkaResolver` sollte früh und verständlich scheitern**,
wenn man ihm einen Container im falschen Scope gibt:

```text
DishkaResolver needs the APP container (app.state.dishka_container), got a
SESSION container. pyrpckit opens the SESSION scope per connection itself.
```

**(5) Channels als Keyword-Argument statt `*args`:**

Heute nimmt `socket(path, /, *channels, ...)` die Channels als Varargs. In Cara
sieht das so aus:

```python
gateway_endpoint = gateway_service.socket(
    "/v1/gateway",
    *RPC_CHANNELS,
    subscription_channel,
    session_channel,
    voice_channel,
    connect=authenticate_rpc_connection,
)
```

- Channel-Liste und Endpoint-Optionen stehen ununterscheidbar in einer Reihe.
  Welches Argument ein Channel und welches eine Option ist, sieht man nur an der
  Position.
- Channels kommen typischerweise gesammelt aus Features (`RPC_CHANNELS`). Die
  müssen erst ausgepackt und mit einzelnen Channels zu einer flachen Reihe
  gemischt werden.
- Die Signatur erzwingt „mindestens ein Channel“ erst zur Laufzeit.

Vorschlag: ein explizites, benanntes Argument, das eine Sequenz nimmt:

```python
def socket(
    self,
    path: str,
    /,
    *,
    channels: Sequence[RpcChannel],
    name: str | None = None,
    connect: ConnectHook | None = None,
    ...
) -> RpcEndpoint: ...
```

```python
GATEWAY_CHANNELS = (
    *RPC_CHANNELS,
    subscription_channel,
    session_channel,
    voice_channel,
)

gateway_endpoint = gateway_service.socket(
    "/v1/gateway",
    channels=GATEWAY_CHANNELS,
    connect=authenticate_rpc_connection,
)
```

- Die Channel-Liste ist ein benannter Wert, den man woanders definieren,
  importieren und in Tests wiederverwenden kann.
- Der Aufruf liest sich als Konfiguration: Pfad, Channels, Hook.
- Mit Kind-Channels aus Abschnitt 3 schrumpft die Liste weiter, weil pro Feature
  nur noch ein Wurzel-Channel gemountet wird.

## 3. Channels und Namen

### Ist-Zustand in Cara

Für die Wire-Namen `voice.turn.start`, `voice.turn.interrupt`,
`voice.turn.playback_completed` und `voice.event` braucht es zwei Channels, weil
Methodennamen keine Punkte enthalten dürfen:

```python
voice_turn_channel = RpcChannel(
    "voice_turn", namespace="voice.turn", raises=(ResourceNotFoundError,)
)
voice_channel = RpcChannel("voice")

@voice_turn_channel.method(
    "start", raises=(MediaNotConnectedRpcError, VoiceTurnAlreadyActiveRpcError)
)
async def start_voice_turn(...) -> VoiceTurnCommandResult: ...

@voice_channel.event("event")
async def voice_events(...) -> AsyncIterator[VoiceEventMessage]: ...
```

Was dabei hakt:

- **Kein Weg von der Fehlermeldung zur Lösung:** `@channel.method("turn.prepare")`
  ergibt `Invalid RPC method name: 'turn.prepare'`. Dass die Lösung ein zweiter
  Channel mit `namespace=` ist, sagt die Meldung nicht.
- **Channel-Name ohne Funktion:** Der Name muss service-weit eindeutig und
  segment-gültig sein (`"voice_turn"`), taucht aber nie auf dem Wire auf. Er ist
  eine zweite Bezeichnung für etwas, das der Namespace schon eindeutig benennt.
- **Zusammengehöriges liegt getrennt:** `voice.turn.*` und `voice.event` gehören
  fachlich zusammen, landen aber in zwei unabhängigen Channels. Gemeinsame
  `raises=` lassen sich nicht teilen.

### Vorschlag

**(1) Kind-Channels, die Namespace und `raises=` erben:**

```python
voice = RpcChannel("voice")
voice_turn = voice.child("turn", raises=(ResourceNotFoundError,))  # -> "voice.turn"

@voice_turn.method(
    "start", raises=(MediaNotConnectedRpcError, VoiceTurnAlreadyActiveRpcError)
)
async def start_voice_turn(...) -> VoiceTurnCommandResult: ...   # voice.turn.start

@voice.event("event")
async def voice_events(...) -> AsyncIterator[VoiceEventMessage]: ...  # voice.event

gateway_service.socket("/v1/gateway", voice, ...)  # mountet voice inkl. Kinder
```

- `child()` erzeugt Namespace `"<parent>.<segment>"` und einen internen,
  eindeutigen Namen. Es ist kein zweiter Bezeichner nötig.
- `raises=` wird vererbt: Parent → Child → Methode.
- Gemountet wird nur der Wurzel-Channel. Die Prüfung „Name ist gleichzeitig
  Operation und Namespace“ bleibt wie heute.

**(2) Channel-Name optional, Namespace als Default-Identität:**

```python
RpcChannel(namespace="voice.turn")   # name defaults to "voice.turn"
RpcChannel("voice")                  # wie heute: name == namespace
```

Der Name muss dann nur noch eindeutig sein, nicht mehr segment-gültig. Ein Punkt
darin ist unproblematisch, weil er nicht auf dem Wire landet.

**(3) Fehlermeldung, die die Lösung nennt:**

```text
ProtocolDefinitionError: RPC method name 'turn.prepare' contains '.'; method
names are single segments inside the channel namespace. Use
RpcChannel("turn") and @channel.method("prepare"), or channel.child("turn").
```

**(4) `payload=` bei Events in der Doku weglassen.** Der Payload-Typ steht schon
in `AsyncIterator[T]`, und Cara nutzt `payload=` nicht mehr. `payload=` sollte
nur noch für den Sonderfall dokumentiert sein, dass der Wire-Typ vom
Generator-Typ abweicht.

**Unverändert positiv:**
- `raises=` auf Channel-Ebene: `ResourceNotFoundError` steht einmal pro Channel
  statt an jeder Methode.
- Die Built-in-Fehler (`RpcInvalidParamsError`, `RpcInternalError`) sind
  implizit. Das spart ~15 redundante Deklarationen.

## 4. Fehler

- **Positiv:** Stabile String-Codes, aus dem Klassennamen abgeleitet
  (`NoActiveTurnError` → `no_active_turn`, Message „No active turn“). Ein Großteil
  der Fehlerklassen ist jetzt eine Zeile, und der generierte Client hat echte
  Exception-Klassen. Die CLI fängt `NoActiveTurnError` statt `error.code != -32010`.
- **Breaking-Falle:** `RpcRemoteError.code` war früher die Zahl und ist jetzt der
  String-Code (die Zahl heißt `rpc_code`). Code wie `if error.code != -32010`
  kompiliert weiter, ist aber still immer wahr. Das gehört als eigener Punkt ins
  Changelog.
- **Kante bei Suffixen:** Die Ableitung entfernt nur `Error`. Cara benennt
  RPC-Fehler mit `...RpcError`, um sie von Domain-Fehlern zu unterscheiden, und
  bekäme `voice_turn_already_active_rpc`. Man muss `code =` explicit setzen.
  Eventuell `RpcError` als Suffix ebenfalls strippen.
- **Unhandlich:** Einen `RpcInvalidParamsError` aus einem Domain-`ValueError`
  zu mappen braucht `RpcInvalidParamsError(issues=[], message=...)`. `issues`
  ist Pflicht, obwohl es keine Pydantic-Issues gibt. Ein Default `issues=[]`
  wäre angenehmer.
- **Positional vs. Keyword:** Der erste Positionsparameter von `RpcError` ist
  jetzt `details`. `ResourceNotFoundError("Not found: x")` (alter Stil) ergibt
  einen `TypeError: ... does not accept details`. Die Meldung könnte
  „did you mean `message=`?“ vorschlagen.
- **Fehler-Envelope:** `data: {"code": ..., "details": null}` wird jetzt immer
  mitgeschickt, auch beim Parse-Error. Das ist ok, aber eine sichtbare
  Wire-Änderung für Clients, die exakt vergleichen.

## 5. Verbindungs-Lifecycle

- **Bewertung:** Connect-Hook mit `RpcConnection` + `Inject[T]` ist das richtige
  Modell, wenn eine Verbindungsidentität in Handler injiziert werden muss. Für
  Cara bringt er aktuell keinen Nutzen: Die Prüfung in der bestehenden
  FastAPI-Route ist gleich lang, entspricht dem Media-Socket und vermeidet einen
  zweiten, pyrpckit-spezifischen Auth-Weg. Empfehlung: in 0.6 entfernen.
- **Positiv:** Event-Sources werden von der Runtime betrieben. In 0.4 hatte Cara
  die Events *zweimal*: einmal als `@module.event` nur fürs Schema und einmal als
  eigener `EventForwarder`, der tatsächlich gesendet hat.
- **Fehlt: serverseitige Request-Hooks.** Der Changelog nennt Request-Hooks nur
  für generierte Clients. Cara hatte pro RPC ein Log mit Methode, ID, Outcome
  und Dauer. Mit der Library-Runtime gibt es dafür keinen Einhängepunkt, und das
  Log ist bei der Migration entfallen. Wunsch: `on_request`/`on_response`-Hooks
  oder eine Middleware am Service/Endpoint.
- **Fehlt: Close-Info.** Es gibt keinen Weg, Close-Code bzw. -Grund der beendeten
  Verbindung zu erfahren, etwa für ein „disconnected (code=1000)“-Log. Den
  Disconnect sieht man nur indirekt über das Finalisieren des Dishka-SESSION-Scopes.
- **Verhaltensänderung:** Requests werden jetzt nebenläufig verarbeitet
  (`RpcLimits.max_concurrency=32`), vorher sequentiell pro Verbindung. Das ist
  vermutlich gewollt, aber Reihenfolgegarantien (z. B. `subscribe` vor `start`
  vom selben Client) sind nicht mehr implizit. Das sollte dokumentiert sein.

## 6. Generierter Client

- **Positiv:** Typisierte Fehler, Modelle und Namespaces am Paket-Root.
  `with_transports()` ist klarer als die drei alten `from_transport*`.
- **Auth-Header:** `connect()` kennt `socket_factory`, aber kein `headers=`.
  Für einen Bearer-Token muss man eine Closure um `websockets.connect` bauen und
  deren Signatur kennen (`subprotocols: list[str] | None`). Das bleibt bei Cara
  eine handgeschriebene `connect_gateway()`-Datei, die per String-Replace in
  `__init__.py` injiziert wird. Wunsch: `connect(headers=...)` bzw. ein
  Extension-Punkt im Codegen für eigene Dateien und Exporte.
- **Lazy by default:** `connect()` öffnet Sockets erst beim ersten Aufruf.
  Auth- oder Netzwerkfehler tauchen dann an unerwarteter Stelle auf (beim ersten
  `subscribe`), nicht beim Verbinden. Cara nutzt deshalb `eager=True`.
  Für Single-Server-Clients wäre eager der erwartbarere Default.
- **`connect().open()`:** Wer den Client über einen Scope hinaus hält (CLI,
  Voice-Session), braucht `await GatewayRpcClient.connect(...).open()`.
  Das ist ok, aber ein `await GatewayRpcClient.connect(...)` direkt wäre
  natürlicher.
- **Server-Name aus dem Pfad:** Der Endpoint-Name wird aus dem letzten
  Pfadsegment abgeleitet (`/v1/gateway` → `gateway`, vorher `local`). Das ist
  sinnvoll, ändert aber `ServerName`-Enum-Werte im Client.
- **Formatierung:** Der generierte Code ist nicht ruff-formatiert (u. a. fehlt
  die Leerzeile nach dem Header). Cara formatiert per `ruff` nach und braucht
  deshalb einen eigenen Codegen-Wrapper statt `pyrpckit generate --config`.
  Ein Post-Process-Hook im TOML (`format = ["ruff", "format", "-"]`) würde den
  Wrapper überflüssig machen.

## 7. Contract

- **Positiv:** Contract aus dem Service mit echten Endpoint-Pfaden. Cara hatte
  in 0.4 die URL separat hardcodiert.
- **Kante:** `service.contract(base_url=...)` verlangt eine `ws://`-URL. Die
  meisten Apps kennen aber ihre HTTP-Basis-URL (die REST-Clients nutzen dieselbe).
  Eine http→ws-Ableitung im generierten `connect()` fehlt, und Cara macht sie in
  `connect_gateway` selbst.
- **Kante:** `render_contract()` schreibt mit `ensure_ascii=True`. Cara hatte
  `ensure_ascii=False` und rendert deshalb selbst per `render_openrpc`. Dort muss
  man daran denken, `binary_streams=contract.binary_streams` durchzureichen,
  sonst fehlen Streams still. `RpcContract.to_openrpc()` wäre robuster.

## 8. Binary Streams: Voice-Media-Transport (noch nicht umgesetzt)

Idee: den eigenen Voice-Media-Socket
(`/v1/voice-sessions/{voice_session_id}/media`) auf `@channel.stream` +
`service.stream(...)` umstellen, statt eigenem Framing und eigener Auth.

Warum das mit 0.5 nicht geht:
- Der Media-Socket ist **bidirektional**: Der Client schickt Mikrofon-PCM
  (`INPUT_PCM`/`INPUT_END`), der Server schickt Assistant-Audio auf *derselben*
  Verbindung. 0.5 unterstützt nur **Server→Client** (Client→Server und
  bidirektionale Streams wurden explizit entfernt; eingehende Frames führen zu
  `PROTOCOL_ERROR`).
- Der Socket hat **exklusive Ownership** („Voice session already has a media
  owner“) und publiziert Frames push-basiert aus dem `VoiceTurnService`.
  Bei `@channel.stream` ist die Quelle ein Async-Generator, der pro Verbindung
  läuft. Das passt für Output-Audio, aber Ownership-Regeln bräuchten den
  Connect-Hook plus einen eigenen Owner-Registry-Provider.
- Möglich wäre nur ein **Split**: Output-Audio als pyrpckit-Stream, Input-Audio
  weiter über einen eigenen Socket. Das ergibt zwei Sockets pro Voice-Session
  und damit mehr Komplexität als heute.

Was es bräuchte, damit Voice komplett auf pyrpckit laufen kann:
1. Client→Server-Binary-Streams (oder bidirektional), z. B.
   `@channel.stream(direction="client-to-server")` mit
   `AsyncIterator[bytes]` als Parameter.
2. Gemeinsames Framing-Konzept (Sequenz/Turn-Index im Frame), oder Doku, wie
   man das in den Payload legt.
3. Pfad-Variablen (`{voice_session_id}`) als typisierter `Inject`-/Parameterwert
   im Stream-Handler (heute nur über `RpcConnection.path_params` als String).
4. Ein Weg, eine Verbindung pro Ressource exklusiv zu halten (Connect-Hook,
   der mit einer passenden Rejection ablehnt, ist dafür wahrscheinlich
   ausreichend und sollte dokumentiert werden).

Sobald (1) existiert, lohnt sich ein neuer Anlauf, denn Auth-Hook, Pfad-Routing
und der generierte Stream-Client würden den handgeschriebenen `MediaTransport`
im Voice-Client ersetzen.
