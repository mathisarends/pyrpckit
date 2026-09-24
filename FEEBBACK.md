# Feedback: Weg zu einer stabilen, prod-tauglichen pyrpckit (Stand 0.8.0)

Quellen: Code von `pyrpckit/`, die generierte Client-Runtime, Probe-Skripte gegen
0.8.0 und der produktive Aufrufer `../cara/gateway` (pinnt `pyrpckit==0.7.0`).
**[verifiziert]** heißt: per Skript reproduziert.

Aufwand: **S** < ½ Tag · **M** 1–2 Tage · **L** mehrere Tage.
Breaking: ob sich öffentliches Verhalten oder API ändert.

## Umsetzungsstand

| Punkt | Status | Commit | Umsetzung |
|-------|--------|--------|-----------|
| 1 | Erledigt | `482f71d` | Unerwartete Handler-Exceptions werden mit Methode und Traceback geloggt; `RpcResponseContext.error` enthält die ursprüngliche Exception. |
| 2 | Erledigt | `9722301` | Envelope-Validierung wird beim Parsen als `-32600` behandelt; `ValidationError` aus Handlern wird als interner Fehler geloggt und mit `-32603` beantwortet. |
| 3 | Erledigt | `a8bdf10` | Handler-Ergebnisse werden vor der Antwort mit Pydantic validiert; ungültige Ergebnisse werden zu geloggten internen Fehlern. |
| 4 | Erledigt | `83f3970` | Generierte Python- und TypeScript-Clients verwerfen Notifications ohne Listener und begrenzen Queues mit `drop_oldest`; Python und TypeScript bieten die `close`-Policy als Option. |
| 5 | Erledigt | `af1afd6` | Writer-Exceptions setzen den Close-Grund, beenden die Verbindung und lösen `connection_closed` beim Observer aus. |
| 6 | Erledigt | `5826666` | Fehlerhafte Event-Payloads werden geloggt und übersprungen; ein ausgefallener Generator beendet nur seine Quelle. `on_error="close"` ist als Opt-in verfügbar. |
| 7 | Erledigt | `69a258f` | Requests generierter Clients und serverseitige Client-Methods bekommen 30 s Standard-Timeout; `RpcLimits.client_method_timeout` konfiguriert letztere, explizites `None` deaktiviert sie. |
| 8 | Erledigt | `0193456` | `RpcLimits.send_timeout` begrenzt Queue-Wartezeit und Socket-Send; langsame Clients werden mit `POLICY_VIOLATION` und `Client too slow` geschlossen. |
| 9 | Erledigt | `b53b59e` | Ein gecachter Adapter wird für Params, Resultate, Events und Envelope-Serialisierung wiederverwendet; der Event-Codec wird pro Quelle erzeugt. Ein zeitabhängiger Mikro-Benchmark bleibt weg, da er als CI-Test keine verlässliche Regression misst. |
| 10 | Erledigt | `137ec2f` | `max_batch_size` begrenzt Batches; Items laufen parallel und teilen sich die `max_concurrency`-Semaphore mit einzelnen Requests. |
| 11 | Erledigt | `8466337` | Der In-Memory-Testclient liegt wieder in `pyrpckit.testing`, mit `handle()`, Notification-Timeout und `RpcTestStream`; Repository-Tests nutzen dieselbe Implementierung. |
| 12 | Erledigt | `f11fdbb` | `serve_websocket()`, Pre-Accept-Hooks und HTTP 401/403 mit Headern sind umgesetzt. Der im Vorschlag ausdrücklich optionale Connection-Lifespan-Hook wurde weggelassen. |
| 13 | Erledigt | `cc0a196` | `RpcService.socket(..., path_model=RoomPath)` prüft Pfadfelder zur Definition, validiert vor `accept()` und injiziert das typisierte Modell; ungültige Werte ergeben `NOT_FOUND`. Der Parameter heißt `path_model`, weil `path` bereits das URL-Template bezeichnet. |
| 14 | Erledigt | `9ca0272` | `RpcStreamClose`, Error-Mapping für Streams und serialisierte `RpcBinaryOutput.send()`-Aufrufe sind umgesetzt; Pre-Accept-Prüfung kam bereits mit Punkt 12. |
| 15 | Erledigt | `b623b7a` | `RpcService(errors={DomainError: RpcErrorClass})` mappt deklarativ, `strict_errors` prüft `raises=`, doppelte explizite numerische Codes werden gewarnt; Docs empfehlen `data.code`. |
| 16 | Erledigt | Commit dieses Punktes | `@channel.server.subscription()` nimmt ein Params-Modell und liefert einen eigenen Generator pro Subscription. Subscribe/Unsubscribe, `subscriptionId`, Cleanup bei Unsubscribe/Disconnect, Limit, OpenRPC-Erweiterung und generierte Python-/TypeScript-Iteratoren sind umgesetzt. In Python muss eine vorzeitig abgebrochene Schleife den Iterator mit `aclosing()` schließen; `async for` garantiert das allein nicht. |
| 17 | Erledigt | Commit dieses Punktes | `extra_files` und `extra_exports` in `rpcgen.toml` kopieren Python-Hilfsdateien und exportieren ihre eindeutigen Namen im Paket; das Manifest verwaltet sie. Generierte Module haben eine Leerzeile vor Imports und laufen durch Ruff/Prettier. Async-Header-Factories werden je WebSocket-Verbindung neu aufgerufen; HTTP(S)-Overrides werden zu WS(S). |
| 18 | Erledigt | Commit dieses Punktes | Python-Clients melden den ersten Verbindungsverlust über `client.closed`, TypeScript-Clients über `onDisconnect`. `connect()` öffnet alle Server standardmäßig, `lazy=True` bzw. `lazy: true` verschiebt den Aufbau. `with_transports()`/`withTransports()` reichen Stream-Variablen weiter. Python bietet optionalen Reconnect mit Backoff und erneuter Anmeldung aktiver Subscriptions; TypeScript bietet vorerst den Callback zur eigenen Wiederverbindung. |
| 19 | Erledigt | Commit dieses Punktes | `RpcObserver` ist eine Basisklasse mit leeren Hooks; `RpcObserverLike` bewahrt strukturelle Typisierung. Request-Kontexte enthalten die Verbindung, Antwort-Kontexte bereits seit Punkt 1 den Originalfehler. Neue Hooks melden Öffnen, Notifications, binäre Frames und langsame Verbraucher. Das optionale OTel-Extra bleibt weg, damit der Kern ohne zusätzliche Telemetrie-Abhängigkeit bleibt. |
| 20 | Erledigt | `ac8911d` + Commit dieses Punkts | Signatur-Typen werden top-level exportiert; `match()` und `contract()` sind typisiert, Event/Stream-Dekoratoren haben Overloads. Stabile Importmodule sind dokumentiert. CI prüft `pyright --verifytypes` mit einem 86-%-Mindestwert und meldet mehrdeutige/unbekannte öffentliche Symbole. Ein sofortiges 100-%-Gate bleibt weg: Pyright zählt sämtliche öffentlichen Submodule und Pydantic-Typen mit; der aktuelle Wert liegt bei 86,5 %. |
| 21 | Erledigt | `db50faf` | Wire-Validierung bleibt erhalten; vor dem Handler wird das deklarierte Pydantic-Modell rekonstruiert, sodass `type(params)` genau stimmt. |
| 22 | Erledigt | `6fe1c9b` | `RpcEndpoint.create_server(observer=...)` ist ergänzt; Resolver/Context-Aufbau ist geteilt. `RpcDisconnect(close, reason)` und typisierte Close-Codes mit `OTHER`/`raw_close_code` sind konsistent; Duplikatfehler nennen die Namen. Stream-Error-Mapping kam mit Punkt 14. |
| 23 | Erledigt | `e283cd3` | `RpcContract.to_json()` und `.write(path)` geben kanonisches UTF-8-JSON aus; CLI und `--check` verwenden denselben Serializer. |
| 24 | Erledigt | `58b5932` | Unbekannte Client-Method-Antworten warnen; Requests werden direkt serialisiert; Namensableitung ist geteilt; Endpoint-Protokolle werden gecacht; redundanter Beispiel-`code` und der unnötige Lazy Import sind entfernt. Der `params.params`-Fehlertext ist durch Punkt 2 entfallen. |
| 25 | Erledigt | Commit dieses Punktes | Zielversion bleibt `0.8.0`; das Changelog enthält Migrationsschritte und das datierte vorhandene 0.6-Tag. `docs/releasing.md` beschreibt Prüfung, Datierung und Tagging. Auf ausdrücklichen Wunsch gibt es keine Kompatibilitäts-Aliasse, keinen zusätzlichen Handshake-Versionscheck und keinen Cara-Integrationstest. |
| Zusatz: Dishka-Annotationen | Erledigt | Commit dieses Punkts | `dishka.py` verwendet `from __future__ import annotations`; `dishka_router()` und `DishkaResolver` brauchen keine String-Annotationen mehr, obwohl optionale Typen nur unter `TYPE_CHECKING` importiert werden. |
| Zusatz: Endpoint-Filter | Erledigt | Commit dieses Punkts | `RpcEndpoint.protocol` verwendet eine lokale Referenz auf das Service-Protokoll und sprechende Namen für Methoden, Notifications und Subscriptions; die Filterlogik bleibt unverändert. |

## Überblick nach Priorität

| #  | Thema | Prio | Aufwand | Breaking |
|----|-------|------|---------|----------|
| 1  | Unerwartete Handler-Exceptions loggen | P0 | S | nein |
| 2  | `ValidationError` im Handler-Body als Internal Error behandeln | P0 | S | ja (Fehlercode) |
| 3  | Rückgabewerte validieren | P0 | S | ja (strenger) |
| 4  | Client-Notification-Überlauf nicht fatal machen | P0 | S | nein |
| 5  | Writer-Task-Fehler beenden die Verbindung sauber | P0 | S | nein |
| 6  | Ein kaputtes Event beendet nicht den ganzen Socket | P0 | S | nein |
| 7  | Default-Timeouts für Requests und Client-Methods | P0 | S | ja (Default) |
| 8  | Slow-Consumer-Schutz (Send-Timeout) | P1 | M | nein |
| 9  | `TypeAdapter`/Codec cachen | P1 | S | nein |
| 10 | Batches: Limit + parallele Ausführung | P1 | S | nein |
| 11 | `pyrpckit.testing.RpcTestClient` zurückbringen | P1 | M | nein |
| 12 | FastAPI: `serve_websocket()` + Pre-Accept-Hook + 401/403 | P1 | M | nein |
| 13 | Typisierte Pfadvariablen für RPC-Sockets | P1 | M | nein |
| 14 | Streams: Fehler-Mapping + `RpcStreamClose` | P1 | M | nein |
| 15 | Deklaratives Error-Mapping | P1 | M | nein |
| 16 | Subscriptions mit Parametern | P1 | L | nein (additiv) |
| 17 | Codegen-Erweiterungspunkte + Auth-Header-Factory | P2 | M | nein |
| 18 | Client: Reconnect-Signal, `connect()`-Semantik | P2 | M | ja |
| 19 | Observability: Kontext, Hooks, Basisklasse | P2 | M | teilweise |
| 20 | Öffentliche Oberfläche: Exports + vollständige Typen | P2 | S | nein |
| 21 | Handler bekommen Originalklasse statt Wire-Subklasse | P2 | M | ja |
| 22 | API-Konsistenz (serve/create_server/close-Signaturen) | P2 | M | ja |
| 23 | Contract-Export: `to_json()`/`write()` | P3 | S | nein |
| 24 | Kleinkram: Fehlermeldungen, Logs, Duplikate | P3 | S | nein |
| 25 | Release-Prozess: Stabilitätsversprechen + Deprecations | P3 | S | – |

Alle Punkte gehen in **0.8.0**. Jede Änderung wird im `CHANGELOG.md` unter
0.8.0 dokumentiert (Added / Changed / Fixed, Breaking-Changes markiert).

---

## P0 – Korrektheit und Betrieb (vor jedem weiteren Feature)

### 1. Unerwartete Handler-Exceptions loggen [verifiziert]
**Problem:** `RpcServer._rpc_error()` (`server.py`) macht aus jeder unbekannten
Exception still `RpcInternalError()`. `raise RuntimeError("db down")` im
Handler erzeugt beim Client `-32603`, im Log steht **nichts**.

**Vorschlag:**
```python
# server.py
logger = logging.getLogger(LOGGER_NAME)

def _rpc_error(self, error: Exception, method: str | None) -> RpcError:
    if isinstance(error, RpcError):
        return error
    if self._error_mapper is not None and (mapped := self._error_mapper(error)):
        return mapped
    logger.exception("RPC method %s failed", method, exc_info=error)
    return RpcInternalError()
```
Zusätzlich `RpcResponseContext.error: BaseException | None` ergänzen, damit
Observer/OTel (cara nutzt Jaeger) die Exception an den Span hängen können.

**Test:** `caplog` enthält Traceback bei `RuntimeError`, nicht bei `RpcError`
und nicht bei gemappten Exceptions.

### 2. `ValidationError` im Handler-Body ist ein Serverfehler [verifiziert]
**Problem:** `server._validation_error()` meldet jede `pydantic.ValidationError`
als `-32600 Invalid request` (oder `-32602`, wenn `"params"` zufällig im `loc`
steht), auch wenn sie im Handler entsteht (`Model.model_validate(db_row)`).
Der Client ist schuld, der Server loggt nichts.

**Vorschlag:** Params werden bereits in `dispatch._validated_params()`
validiert und dort zu `RpcInvalidParamsError`. Envelope-Fehler kommen aus
`parse_request`. Also:
- `parse_request` fängt `ValidationError` selbst ab → `RpcInvalidRequestError`.
- `_validation_error()` in `server.py` löschen; eine `ValidationError` aus
  `execute()` läuft in Punkt 1 (Log + Internal Error).
- cara kann dann `if isinstance(error, ValidationError): return None` aus dem
  Mapper streichen.

**Test:** Handler, der `Model.model_validate({})` aufruft → `-32603` + Log.
Ungültige Params → weiterhin `-32602` mit Issues.

### 3. Rückgabewerte validieren [verifiziert]
**Problem:** `-> R` mit `return {"nope": 1}` bzw. `-> int` mit `return "abc"`
geht unverändert über die Leitung, nur mit Pydantic-`UserWarning`. Der
generierte Client wirft dann `RpcResponseValidationError`, weit weg von der
Ursache.

**Vorschlag:** In `RpcDispatcher.execute()` das Ergebnis durch den (gecachten,
siehe 9) Result-Adapter schicken:
```python
result = await function(**arguments)
return method.result_adapter.validate_python(result)  # strict=False, from_attributes=True
```
Fehler → Punkt 1 (Log + `-32603`). Falls die Kosten stören:
`RpcService(validate_results: bool = True)`, aber Default **an**.

**Test:** Falscher Rückgabetyp → `-32603` + Log; `dict` passend zum Modell →
wird akzeptiert und korrekt serialisiert.

### 4. Client-Notification-Überlauf nicht fatal machen
**Problem:** Die generierte Python-Transport-Schicht
(`templates/python/transport.py.j2`, `_receive`) wirft bei voller
Notification-Queue (`notification_queue_size=100`) `RpcTransportError` und
beendet **die ganze Verbindung inklusive laufender Requests**. Da der
Server Events ab Connect ungefragt pusht und der Pump erst beim ersten
`subscribe()` startet, stirbt ein Client, der nur Methoden aufruft, nach 100
Events.

**Vorschlag:**
- Transport liefert Notifications nur an registrierte Listener; ohne Listener
  werden sie verworfen (optional mit Debug-Log).
- Pro Subscriber eine begrenzte Queue mit Policy `drop_oldest` + einmaliger
  `warning`, statt Verbindungsabbruch. Konfigurierbar:
  `connect(notification_overflow="drop_oldest" | "close")`.
- Gleiches im TypeScript-Template prüfen.

**Test:** Server pusht 1000 Events, Client ruft nur Methoden → Requests
funktionieren weiter.

### 5. Writer-Fehler beenden die Verbindung sauber
**Problem:** In `runtime.serve_endpoint` fängt `writer()` keine Exceptions. Wirft
`socket.send` etwas anderes als eine vom Reader gleichzeitig bemerkte
Trennung (z. B. `RuntimeError` von Starlette), stirbt der Task still,
`close_event` wird nie gesetzt, die Verbindung hängt.

**Vorschlag:** Wie `events()` einpacken:
```python
async def writer():
    try:
        while True:
            await socket.send(await outgoing.get())
    except asyncio.CancelledError:
        raise
    except RpcDisconnect as error:
        nonlocal client_closed
        client_closed = True
        request_close(error.code or RpcConnectionClose.NORMAL, error.reason)
    except Exception:
        logger.exception("RPC writer failed")
        request_close(RpcConnectionClose.INTERNAL_ERROR, "Internal error")
```
**Test:** Fake-Socket, dessen `send` wirft → `serve()` kehrt zurück, Observer
bekommt `connection_closed`.

### 6. Ein kaputtes Event beendet nicht den ganzen Socket
**Problem:** In `runtime._event_source` führt eine Exception im Generator oder
ein Payload, der die Validierung nicht besteht, zu
`request_close(INTERNAL_ERROR)` für den **ganzen** RPC-Socket, also inklusive
aller anderen Event-Quellen und laufenden Requests.

**Vorschlag:**
- Einzelnes ungültiges Item: `logger.exception(...)` und überspringen.
- Generator bricht ab: nur diese Quelle beenden und loggen.
- Opt-in für das alte Verhalten: `@channel.server.event(on_error="close")`.

**Test:** Zwei Event-Quellen, eine wirft → die andere liefert weiter, Requests
funktionieren.

### 7. Default-Timeouts
**Problem:** `request_timeout=None` im generierten Client und
`RpcConnectedClient.call(..., timeout=None)` auf dem Server: ein hängendes
Gegenüber blockiert den Aufrufer für immer.

**Vorschlag:** Default 30 s (analog zu `stream.end()`), `None` bleibt explizit
möglich. Serverseitig als `RpcLimits.client_method_timeout: float | None = 30.0`,
damit es pro Endpoint konfigurierbar ist. In CHANGELOG als Verhaltensänderung
markieren.

---

## P1 – Robustheit und Ergonomie, die in cara direkt Code löscht

### 8. Slow-Consumer-Schutz
**Problem:** Antworten, Events und Client-Method-Requests teilen sich eine
`asyncio.Queue(max_queue_size)`. Ein langsamer Client lässt alle
`outgoing.put(...)` blockieren, also auch Handler und Event-Quellen, ohne
Timeout und ohne Metrik.

**Vorschlag:** `RpcLimits.send_timeout: float | None = 10.0`. Hängt ein
`put`/`send` länger, wird mit `POLICY_VIOLATION` und dem Grund
`"Client too slow"` geschlossen und das geloggt. Optional später getrennte
Queues für Antworten (Priorität) und Events.

### 9. `TypeAdapter` und Codec cachen
**Problem:** Wird pro Nachricht neu gebaut:
- `dispatch._validated_params`: `TypeAdapter(method.params)`,
- `envelopes`: `TypeAdapter(...)` in jedem `field_serializer`,
- `runtime._event_source`: `RpcCodec()` pro Event.

**Vorschlag:** Adapter als `cached_property`/Feld an den Definitionen
(`RpcMethodDefinition.params_adapter`, `.result_adapter`,
`RpcNotificationDefinition.payload_adapter`) oder ein modulweites
`@cache def adapter(annotation)` wie in `connected_client._adapter`.
Envelopes bekommen statt der Annotation direkt den Adapter.
Mikro-Benchmark in `tests/` (Events/s) als Regressionsschutz.

### 10. Batches: Limit und Parallelität
**Problem:** `RpcServer.handle` arbeitet Batches mit
`[await ... for item in batch]` sequentiell ab, hat kein Größenlimit und
belegt nur einen Slot der `max_concurrency`-Semaphore.

**Vorschlag:** `RpcLimits.max_batch_size: int = 32` (`0` = Batches ablehnen mit
`-32600`), innerhalb des Batches `asyncio.gather` begrenzt durch dieselbe
Semaphore.

### 11. `pyrpckit.testing.RpcTestClient` zurückbringen
**Problem:** Seit 0.7 entfernt. cara hat ihn mit 165 Zeilen nachgebaut
(`tests/presentation/websocket/_rpc_test_client.py`) und importiert dafür
interne Module (`pyrpckit.dependencies.RpcResolverLike`). Das baut jeder
Nutzer.

**Vorschlag:** Kleine, stabile API als Teil der Distribution:
```python
async with RpcTestClient(service, "/v1/gateway", headers=..., resolver=...) as client:
    result = await client.request("turn.start", {"sessionId": ...})
    await client.notify("x.y", {...})
    method, params = await client.next_notification(timeout=1)
    client.handle("room.media.play", handler)   # Client-Methods beantworten
with pytest.raises(RpcTestError) as error: ...  # .code, .rpc_code, .details
```
Plus `RpcTestStream` für Binär-Streams (`send_frame`, `end_input`,
`receive_frame`, `closed`). cara's Implementierung ist eine gute Vorlage.

### 12. FastAPI: `serve_websocket()`, Pre-Accept-Hook, 401/403
**Problem:** cara nutzt `create_router`/`dishka_router` **nicht**, weil vor
`serve()` authentifiziert werden muss. Pro Route wird daher dupliziert:
`FastApiSocket(...)`, `DishkaResolver(websocket.app.state.dishka_container)`,
`except asyncio.CancelledError: return`, eine eigene 401-Antwort mit
`WWW-Authenticate` (`RpcRejection` kennt kein `UNAUTHORIZED` mehr) und
`registry.connect/disconnect` rund um `serve()`.

**Vorschlag (drei unabhängige Schritte):**
1. Öffentlicher Helper, der Socket-Wrapping und CancelledError kapselt:
   ```python
   await pyrpckit.fastapi.serve_websocket(endpoint, websocket, resolver=...)
   ```
2. `RpcRejection.UNAUTHORIZED` (401) und `FORBIDDEN` (403) wieder
   aufnehmen, `RpcSocket.reject(rejection, reason, *, headers=None)`.
   Das ist Transport-Vokabular, keine Auth-Logik in der Lib.
3. Pre-Accept-Hook pro Service/Endpoint, der ablehnen darf und Kontext
   liefern kann:
   ```python
   async def authenticate(handshake: RpcHandshake) -> Mapping[type, object] | None:
       if not ok(handshake.headers.get("authorization")):
           raise RpcReject(RpcRejection.UNAUTHORIZED, "Unauthorized",
                           headers={"WWW-Authenticate": "Bearer"})
       return {Principal: principal}
   create_router(service, before_accept=authenticate)
   ```
Optional: `lifespan=` pro Endpoint (async-contextmanager um die Verbindung),
damit `registry.connect/disconnect` ohne Dishka-SESSION-Provider geht.

**Ergebnis in cara:** `gateway/presentation/websocket/router.py` schrumpft auf
`create_router(...)`.

### 13. Typisierte Pfadvariablen für RPC-Sockets
**Problem:** Streams validieren Pfadvariablen (`voice_session_id: UUID`),
Sockets nicht. cara schreibt in jeder Room-Methode
`UUID(connection.path_params["room_id"])`, und ungültige IDs werden erst im
Handler zu `invalid_params` statt beim Handshake zu 404.

**Vorschlag:**
```python
class RoomPath(RpcModel):
    room_id: UUID

room_endpoint = service.socket("/v1/rooms/{room_id}/channel",
                               channels=(room_channel,), path=RoomPath)

@room_channel.server.method()
async def report_state(params: RoomStateReport, path: Inject[RoomPath], ...) -> ...:
    registry.report_state(path.room_id, params.state)
```
Validierung vor `accept()` in `runtime._prepare` (es gibt dort schon
`path_model` für Streams), Fehler → `NOT_FOUND`. Zur Definitionszeit
prüfen, dass die Felder den `{variablen}` des Pfads entsprechen.

### 14. Streams: Fehler-Mapping und `RpcStreamClose`
**Problem:** `RpcStreamEndpoint` hat kein `error_mapper`. `await
sessions.get(id)` im Voice-Stream wirft bei unbekannter Session einen
Stacktrace-Log und Close `1011 INTERNAL_ERROR`, obwohl es ein 404 ist. cara
fängt `ValueError` ab und ruft `connection.close(...)` von Hand.

**Vorschlag:**
- Neue Exception `RpcStreamClose(close: RpcConnectionClose, reason: str)`,
  die ein Stream-Handler werfen darf → sauberer Close ohne Traceback-Log.
- `error_mapper` auch für Streams: gemappte `RpcError` → Close
  `POLICY_VIOLATION` mit `"{code}: {message}"` als Reason.
- Pre-Accept-Check aus 12 auch für Streams, damit „nicht gefunden“ als HTTP
  404 beim Handshake landet.
- `RpcBinaryOutput.send` intern mit Lock serialisieren und das dokumentieren
  (cara hält dafür einen eigenen `send_lock` pro Session).

### 15. Deklaratives Error-Mapping
**Problem:** cara's `gateway_error_mapper` ist eine lange `isinstance`-Kette
mit Namenskollisionen (`NoActiveTurnError` Domain vs. RPC → Import-Alias).
Numerische Codes werden von Hand vergeben (`-32004`, `-32009` …), obwohl der
String-`code` der eigentliche Identifikator ist. Eine gemappte, aber in
`raises=` nicht deklarierte Fehlerklasse fällt nirgends auf.

**Vorschlag:**
```python
class ResourceNotFoundError(RpcError, maps=(SessionNotFoundError, AlarmNotFoundError)):
    pass   # message default: str(original)
```
oder alternativ `RpcService(errors={SessionNotFoundError: ResourceNotFoundError})`.
Der `error_mapper` bleibt als Fallback.
- In den Docs festhalten: Clients werten `data.code` aus, `rpc_code` ist
  optional. Wenn `rpc_code` doppelt vergeben ist, zur Freeze-Zeit warnen.
- `RpcService(strict_errors=True)`: nicht deklarierte `RpcError` einer Methode
  werden geloggt (Debug) bzw. als Internal Error behandelt (strict).

### 16. Subscriptions mit Parametern
**Problem:** Größte Lücke im Kern-Use-Case. Events laufen pro Verbindung
global und ungefragt. cara baut „Events für Session X“ komplett selbst:
`subscription.subscribe/unsubscribe/subscribe_channel/...` als normale
Methoden, `InMemoryEventStream` mit Client-IDs, `GatewayClient`-Provider,
unbounded `EventReceiver`-Queues und `while True: yield`. Außerdem wirkt
`@channel.server.event("event")` wie ein Workaround für „ein Event pro
Channel“.

**Vorschlag:**
```python
@session_channel.server.subscription()
async def events(params: SessionParams, bus: Inject[EventBus]) -> AsyncIterator[SessionEvent]:
    async with bus.listen(params.session_id) as sub:
        async for event in sub:
            yield event
```
- Wire: `session.events.subscribe(params) → {subscriptionId}`,
  Notification `session.events` mit `{subscriptionId, payload}`,
  `session.events.unsubscribe({subscriptionId})`.
- Serverseitig: ein Task pro Subscription. Unsubscribe/Disconnect → `aclose()`
  des Generators, also greift das `finally` des Nutzers. Limit
  `RpcLimits.max_subscriptions`.
- Client: `async for e in client.session.events(session_id=...)`, Abbruch
  der Schleife → Unsubscribe.
- OpenRPC: eigenes Extension-Feld `x-rpc-subscriptions`.
- Bestehende `server.event` bleibt als „Broadcast ohne Params“.

Löst Punkt 4 grundsätzlich und erlaubt später Re-Subscribe nach Reconnect (18).

---

## P2 – Client-Robustheit, Observability, API-Oberfläche

### 17. Codegen-Erweiterungspunkte und Auth-Header-Factory
**Problem:** cara's `client_codegen.py` patcht generierten Code per
`str.replace` in `__init__.py`, ändert `.rpcgen/manifest.json` von Hand, fügt
`authenticated.py` hinzu (mit manueller `http→ws`-Konvertierung) und lässt
`ruff check --fix-only` + eine eigene Header-Leerzeilen-Korrektur laufen.

**Vorschlag:**
- `rpcgen.toml`: `extra_files = ["src/extra/authenticated.py"]` und
  `extra_exports = ["connect_gateway"]` → landen in `__init__` und Manifest.
- Generierter Header mit Leerzeile vor den Imports, Import-Reihenfolge so, dass
  Standard-Ruff (`I`-Regeln) nichts ändert. CI-Test: generierte Beispiele mit
  `ruff check --select I,E,F` und `ruff format --check`.
- `connect(url=...)` akzeptiert `http(s)://` und konvertiert (wie der Contract).
- `headers: Mapping[str, str] | Callable[[], Awaitable[Mapping[str, str]]]`
  für Token-Refresh.

### 18. Client: Verbindungsverlust sichtbar machen, `connect()` vereinfachen
**Problem:**
- Kein Signal „Verbindung verloren“ außer `RpcTransportError` in offenen
  Iteratoren, kein Reconnect.
- `connect()` liefert ein Objekt, das awaitable **und** Context-Manager ist.
  `eager` ist bei Single-Server standardmäßig an, bei Multi-Server aus. cara
  nutzt `eager=False` + `await connection`: schwer vorhersagbar.
- `with_transports(...)` reicht `variables` nicht an `__init__` weiter.

**Vorschlag:**
- `client.closed: asyncio.Future[BaseException | None]` bzw.
  `on_disconnect=callback`.
- Semantik vereinheitlichen: `await connect()` / `async with connect()` öffnen
  immer eager. Lazy nur mit explizitem `lazy=True`.
- `with_transports(..., variables=...)` ergänzen (Bugfix, S).
- Auto-Reconnect mit Backoff + Re-Subscribe erst nach 16.

### 19. Observability
**Problem:** `RpcRequestContext` hat keine `RpcConnection`/Endpoint-Info, es
fehlen `connection_opened` und Hooks für Events/Stream-Frames. Observer ist ein
`Protocol` mit drei Pflichtmethoden; fehlende Methoden erzeugen bei jedem
Aufruf `logger.exception`.

**Vorschlag:**
- `class RpcObserver` als Basisklasse mit No-op-Defaults (Protocol bleibt als
  Typ verfügbar).
- `RpcRequestContext.connection: RpcConnection | None`,
  `RpcResponseContext.error: BaseException | None` (siehe 1).
- Neue Hooks: `connection_opened`, `notification_sent(name, size)`,
  `slow_consumer_closed` (siehe 8).
- Optional `pyrpckit.otel` als Extra mit fertigem Observer (cara hat OTel).

### 20. Öffentliche Oberfläche: Exports und Typen
**Problem:** In öffentlichen Signaturen tauchen `RpcResolverLike`,
`RpcResponseMessage`, `RpcProtocol` auf, stehen aber nicht in `__all__`. cara
importiert aus Submodulen (`pyrpckit.dependencies`, `pyrpckit.server`,
`pyrpckit.service`). `RpcService.match()` hat keinen Rückgabetyp,
`RpcService.contract(variables=None)` keine Typen, `server.event/stream` geben
`Any` zurück (keine `@overload`s wie `method`).

**Vorschlag:**
- Alles, was in einer öffentlichen Signatur vorkommt, top-level exportieren.
- Submodule als intern markieren (`_runtime.py` …) oder in den Docs eine Liste
  „stabile Module“ führen.
- `pyright --verifytypes pyrpckit` in CI (meldet unvollständig getypte
  öffentliche Symbole).

### 21. Handler bekommen eine Wire-Subklasse statt des deklarierten Modells [verifiziert]
**Problem:** `wire_annotation` erzeugt für jedes Nicht-`RpcModel` eine
Subklasse gleichen Namens. Im Handler gilt `type(params) is P → False`.
Überraschend bei `match`, Equality, Pickling, Logs.

**Vorschlag (eins von beiden):**
- Wire-Modell nur für Schema/Validierung nutzen und danach per
  `P.model_construct(**validated.__dict__)` bzw. `P.model_validate(...,
  by_name=True)` in die Originalklasse überführen.
- Oder: Nur `RpcModel` erlauben und fremde `BaseModel` mit klarer Meldung
  ablehnen („erbe von RpcModel oder setze alias_generator=to_camel“).

### 22. API-Konsistenz
**Problem und Vorschlag im Einzelnen:**
- `RpcChannel.create_server` nimmt `observer`, `RpcEndpoint.create_server`
  nicht; `RpcStreamEndpoint.serve` hat kein `error_mapper`. → eine gemeinsame
  Signatur, die Resolver+Context-Logik in einer Hilfsfunktion statt doppelt.
- `RpcDisconnect(reason, code=...)` vs. `RpcConnection.close(close, reason=)`:
  inverse Reihenfolge. → beide `(close: RpcConnectionClose, reason: str = "")`.
- `close_code: RpcConnectionClose | int | None` → immer `RpcConnectionClose`
  (unbekannte Codes auf ein `OTHER` abbilden), roher Int als `raw_close_code`.
- `RpcChannel(name, namespace=...)`: Unterschied in der Fehlermeldung
  erklären; „channel names must be unique“ soll die kollidierenden Namen
  nennen.

---

## P3 – Feinschliff und Prozess

### 23. Contract-Export
**Problem:** cara ruft `render_openrpc(contract.protocol, title=..., servers=...,
binary_streams=...)` von Hand auf und serialisiert JSON selbst;
`contract.to_openrpc()` ist nicht bekannt.

**Vorschlag:** `contract.to_json() -> str` (kanonisch: `indent=2`,
`ensure_ascii=False`, `\n` am Ende) und `contract.write(path)`. CLI und
`--check` nutzen dieselbe Funktion. In den Docs als einzigen Weg zeigen.

### 24. Kleinkram
- Positionale Params (`[...]`) → Meldung `Invalid params at params.params`:
  doppeltes `params` entfernen. In den Docs sagen, dass nur Params per Name
  unterstützt werden.
- Antworten auf Server-Requests mit unbekannter ID: Log von `DEBUG` auf
  `WARNING` heben.
- `RpcConnectedClient._request_frame`: `model_dump()` + erneute Validierung
  durch `adapter.dump_python(params, mode="json", by_alias=True)` ersetzen.
- `_request_name`-Logik doppelt in `channel.py` und `service.py` → eine Funktion.
- `RpcEndpoint.protocol` baut bei jedem Zugriff ein neues `RpcProtocol`, pro
  Verbindung mehrfach → cachen.
- Redundantes `code = "..."` in Beispielen vermeiden. Die Docs sollten die
  automatische Ableitung (`VoiceTurnAlreadyActiveRpcError` →
  `voice_turn_already_active`) prominent zeigen.

### 25. Release-Prozess
**Problem:** Fast jedes Release seit 0.5 hat Breaking Changes, 0.6–0.8 sind
„Unreleased“, cara hängt auf 0.7.0.

**Vorschlag:**
- Releases datieren und taggen.
- Deprecation-Policy: umbenannte Namen ein Minor-Release lang als Alias mit
  `DeprecationWarning` (z. B. `RpcPeer = RpcConnectedClient`).
- Vor 1.0: Migrationshinweis pro Release im CHANGELOG („so ändert sich euer
  Code“).
- Client/Server-Versionscheck: Protokoll-`version` im Handshake (Subprotocol
  `pyrpckit.v{n}` oder Header). Ein veralteter generierter Client scheitert dann
  früh mit klarer Meldung statt mit `method_not_found`.
- cara als Integrationstest nutzen: vor jedem Release gegen cara's Testsuite
  laufen lassen.

**Entscheidung für 0.8.0:** Breaking Changes sind während der 0.x-Releases
erlaubt; Verbraucher generieren ihre Clients nach dem Upgrade neu. Daher gibt
es keine Kompatibilitäts-Aliasse und keinen zusätzlichen Versions-Handshake.
Der Cara-Integrationstest entfällt auf ausdrücklichen Wunsch. Release-Datum
und Tag werden erst beim tatsächlichen Release gesetzt.
