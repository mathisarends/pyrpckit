# Review: generierte Clients (Python & TypeScript)

Grundlage: `examples/generated_clients/` (Fixture `automation.openrpc.json`,
Generator 0.6.0, `layoutVersion` 9). Betrachtet werden Struktur, Ergonomie und
die Symmetrie zwischen beiden Sprachen — nicht die Laufzeit-Interna.

---

## 0. Wie Connect und Multi-Socket heute funktionieren

Der Contract kennt drei *Server* (`production`, `browser`, `streaming`) plus
einen Binary-Stream (`screencast`). Jede Methode trägt in `routes.*` ihren
Server:

```ts
browserTabsOpen: { method: "browser.tabs.open", server: "browser" }
```

`RpcClientCore` hält daher **eine Map `server -> RpcTransport`** statt eines
Transports. `#transportFor(server)` wählt pro Request den Socket aus; ohne
`server` und bei mehr als einem Transport wirft es. `connect()` ist nur Zucker
darüber: es löst alle Server-URLs auf (`resolveEndpoints`), öffnet **für jeden
Server eine WebSocket-Verbindung**, sammelt sie in einem Objekt und übergibt das
dem Konstruktor. Der Binary-Stream läuft an all dem vorbei: er ist kein Server,
sondern eine eigene URL-Vorlage, die erst beim Aufruf von `client.screencast()`
zu einem separaten Socket wird.

Das Modell an sich ist richtig — ein Client, n Sockets, Routing über den
Contract. Die Umsetzung hat aber Löcher (Abschnitt 1) und die Oberfläche ist
zu roh (Abschnitt 2).

---

## 1. Blocker — kaputt, nicht nur unschön

### 1.1 Beide Clients sind nicht importierbar, wenn der Contract keine benannten Fehler hat

`transport.py` / `transport.ts` importieren **bedingungslos**
`error_from_response` / `errorFromResponse` aus `./errors`, aber die Datei wird
nur erzeugt, wenn `_named_errors(ir)` wahr ist
(`pyrpckit/codegen/python.py:84`, `pyrpckit/codegen/typescript.py:60`).
Das Fixture hat keine benannten Fehler — also:

```
>>> import automation_client
ModuleNotFoundError: No module named 'automation_client.errors'
```

TypeScript: `typescript/errors.ts` fehlt ebenso, steht nicht im Manifest, und
`transport.ts:5` importiert es trotzdem → kompiliert nicht. Der committete
Beispiel-Output ist damit in beiden Sprachen unbenutzbar.

**Fix:** `errors`-Modul immer emittieren (Basisklassen + `error_from_response`),
die generierten Unterklassen nur anhängen, wenn es welche gibt. Zusätzlich:
Smoke-Test im Generator-Testlauf, der den Output importiert bzw. `tsc --noEmit`
darauf laufen lässt.

### 1.2 TypeScript exportiert seine Fehlertypen nicht

`RpcRemoteError` und `RpcConnectionClosed` leben in `core.ts`, `index.ts`
exportiert `core.ts` aber nie. Ein Konsument kann keinen `instanceof`-Check
schreiben — der Standardfall für einen RPC-Client. Python exportiert die
Hierarchie sauber aus `__init__.py`; TS muss nachziehen.

### 1.3 Python schickt Defaults, die der Server setzen sollte

```python
async def start(self, *, quality: int = 80) -> None:
    params = StartScreencastParams(quality=quality)
    await self._rpc.request(..., params=params.model_dump(exclude_unset=True))
```

`quality` wird *immer* explizit gesetzt, `exclude_unset=True` ist damit
wirkungslos. `client.browser.screencast.start()` sendet `{"quality": 80}`,
obwohl der Nutzer nichts angegeben hat. Folge: der Server-Default ist tot, und
eine Änderung des Defaults auf Serverseite erreicht alte Clients nie.

`internal/unset.py` mit `UNSET`/`UnsetType` wird generiert und **nirgends
benutzt** — offensichtlich genau dafür gedacht. Signatur soll sein:

```python
async def start(self, *, quality: int | UnsetType = UNSET) -> None:
```

TS macht es über `withoutUndefined()` bereits richtig; hier ist Python das
Problem.

### 1.4 Notification-Streams hängen still, sobald der Pump einmal beendet ist

TS: `#pumps` ist ein `WeakSet`, das nie geleert wird. Endet die Notification-
Iteration des Transports (Socket zu), werden alle aktuellen Subscriber beendet —
ein *späteres* `client.tasks.updated()` registriert sich aber in dasselbe
`subscribers`-Set, startet keinen Pump mehr (`#pumps.has` ist true) und
**wartet für immer**, ohne Fehler.

Python: identisch über `_stream_tasks[key]` — der fertige Task bleibt in der
Map, der neue Subscriber bekommt eine Queue, die niemand mehr befüllt.

**Fix:** Endzustand pro Transport merken und neuen Subscribern sofort das
terminale Ergebnis (Ende oder Fehler) geben; Pump-Eintrag beim Beenden entfernen.

### 1.5 `connect()` öffnet alle Sockets, immer

Wer nur `tasks.list()` braucht, bezahlt drei WebSocket-Handshakes; fällt
`browser` aus, ist der ganze Client tot. TS öffnet zudem **sequenziell**
(`for ... await`), also Latenz = Summe statt Maximum. Python macht es in
`ClientConnection.__aenter__` genauso.

**Fix:** Sockets lazy pro Server beim ersten Request öffnen (mit
Deduplizierung), und wenn eager, dann parallel (`Promise.all` /
`asyncio.gather`). Optional `connect({ only: ["production"] })`.

### 1.6 Binary-Streams erben nichts vom Connect

`connect(endpoints.streaming({ host: "stage.internal" }))` beeinflusst
`client.screencast()` nicht — die Stream-URL hat ihre *eigene* `host`-Variable
mit eigenem Default (`stream.example.com`). Der Host muss zweimal, an zwei
völlig verschiedenen Stellen, gesetzt werden. Der Client muss die beim Connect
aufgelösten Variablen speichern und als Default für Streams verwenden.

Dazu passend: `connect()` verdrahtet `BinaryWebSocketStream.open` fest. Weder
`socketFactory`/`socket_factory` noch `incomingQueueSize` kommen je beim
Stream-Opener an — die Parameter existieren, sind aber über den offiziellen
Einstiegspunkt nicht erreichbar (in Tests also nicht mockbar).

### 1.7 Zwei verschiedene URL-Auflösungen

`endpoints.py` nutzt `RpcServerInfo.resolve()` (prüft unbekannte Variablen und
`enum`). `client.screencast()` macht stattdessen inline:

```python
endpoint_url = url or "wss://{host}/browser/screencast"
endpoint_url = endpoint_url.replace("{host}", host)
```

Keine Enum-Prüfung, keine Fehlermeldung bei Tippfehlern, und die Vorlage ist
dupliziert, obwohl `BINARY_STREAMS` sie schon enthält. TS hat dafür
`resolveStreamEndpoint` — Python soll dieselbe Route gehen.

### 1.8 `BinaryStreamOpening` ist ein Leak-Footgun

Das Objekt ist gleichzeitig awaitable und Async-Context-Manager:

```python
conn = await client.screencast()          # nie geschlossen
async with client.screencast() as conn:   # korrekt
```

Beide Formen sehen identisch aus, nur eine räumt auf. Zweitens schließt
`__aexit__` nur, wenn `__aenter__` gelaufen ist — und `_connection` wird bei
Mehrfachnutzung überschrieben. Empfehlung: nur den Context-Manager anbieten
(`__await__` streichen) oder explizit `await ....open()` verlangen.

### 1.9 Binary-Streams enden nie regulär

Python `__anext__` delegiert an `receive()`, das bei geschlossenem Socket einen
`RpcTransportError` wirft; TS iteriert `while (true)`. Ein `async for frame in
stream` kann also nur per Exception verlassen werden. Ein normaler
Serverabschluss (`direction: server-to-client`, Server ist fertig) sollte
`StopAsyncIteration` / `done: true` bedeuten, nicht einen Fehler.

### 1.10 Tote Features im generierten Client

`RpcClientHook` (before_request/after_response) existiert in
`internal/core.py`, aber `AutomationClient.__init__` reicht `hooks` nicht durch
— der einzige Erweiterungspunkt für Auth, Logging und Tracing ist von außen
nicht erreichbar. TS hat gar kein Äquivalent.

---

## 2. Ergonomie der öffentlichen Oberfläche

### 2.1 `screencast` gehört an den Namespace, nicht an die Wurzel

Heute:

```ts
await client.browser.screencast.start({ quality: 80 });  // RPC
const frames = await client.screencast({});              // Binary-Socket
```

Zwei Aufrufe, die dasselbe Feature bedienen, an zwei Ebenen — und an der Wurzel
kollidiert der Stream namentlich mit dem Namespace darunter.

Ursache ist nicht der Generator allein: `names.client_view()` leitet die
Platzierung aus `BinaryStreamDecl.path` ab, und `path` entsteht in
`ir._binary_streams()` aus `name.split(".")`. Das Fixture deklariert den Stream
als `"screencast"` — ohne Punkt, also Wurzel. Serviceseitig hängt
`channel.stream()` den Channel-Namespace bereits an (`join_rpc_name`), ein auf
dem `browser`-Channel deklarierter Stream hieße also `browser.screencast`.

Zwei Dinge sind zu tun:

1. **Fixture/Contract korrigieren**, damit der Beispiel-Output den Normalfall
   zeigt (`browser.screencast.frames`).
2. **Generator härten**: ein Stream, dessen Name auf einen bestehenden
   Namespace-Knoten fällt (`browser.screencast` vs. Klasse `BrowserScreencast`),
   muss eine klare Fehlermeldung erzeugen statt still zu kollidieren —
   `assert_unique_names` deckt Streams gegen Kindknoten derzeit nicht ab.
   Empfehlung: Streams brauchen ein eigenes Verb (`frames`, `chunks`), genau wie
   Methoden.

### 2.2 Zu viele Konstruktionswege

Python: `AutomationClient(...)`, `from_transport`, `from_transports`,
`from_transport_map`, `connect`. TS: Konstruktor, `fromTransport`,
`fromTransports`, `connect`. Davon sind zwei redundant (`from_transports` ist
`from_transport_map` mit Pflicht-Keywords für *alle* Server — bei vier Servern
unbrauchbar). Ziel: **ein** `connect()` für 95 % der Fälle, **ein**
`with_transports()` für Tests/Sonderfälle, Konstruktor `private`/undokumentiert.

### 2.3 `connect()` ist in beiden Sprachen unterschiedlich geformt

| | Python | TypeScript |
|---|---|---|
| Aufruf | `async with Client.connect(...)` | `await Client.connect(...)` |
| Overrides | `*endpoint_overrides` (varargs) | `{ endpoints: [...] }` |
| Rückgabe | `ClientConnection` (nur CM, `await` geht nicht) | `Promise<Client>` |

Dass `await Client.connect()` in Python nichts öffnet, sondern nur ein
Hilfsobjekt liefert, ist eine echte Stolperfalle. Und in beiden Sprachen ist der
häufigste Fall — „alles läuft auf *einem* Host" — der umständlichste: man muss
pro Server eine Endpoint-Factory aufrufen.

### 2.4 Parameter-Stil ist asymmetrisch

Python flacht ab (`open(*, url: str)`), TS nimmt ein Objekt
(`open(params: OpenTabParams)`). Für TS ist das bei einem einzigen Pflichtfeld
unnötig sperrig; umgekehrt verliert Python die Möglichkeit, ein fertiges Modell
durchzureichen. Vorschlag: TS bekommt die Params weiterhin als Objekt
(idiomatisch), aber **optionale Params-Objekte werden optional** (`start()`
statt `start({})`), und Python akzeptiert zusätzlich das Modell positional:
`open(OpenTabParams(url=...))` oder `open(url=...)`.

### 2.5 TypeScript: Namespace-Typen und Routen sind nicht exportiert

`Tasks`, `Browser`, `BrowserTabs` stehen in `namespaces/`, `index.ts` gibt sie
nicht heraus. Damit lässt sich keine Funktion schreiben, die
`client.browser.tabs` entgegennimmt. Gleiches gilt für `routes`, `RpcRouteInfo`
und `RpcClientCore`.

### 2.6 Python: Modelle sind nicht aus dem Paket importierbar

`__init__.py` re-exportiert Transport, Endpoints, Streams und Fehler — aber
nicht `models`. `from automation_client import Task` schlägt fehl, während TS
`export type * from "./models"` macht. Das ist die sichtbarste Asymmetrie für
den Alltag.

### 2.7 Validierungsgarantien unterscheiden sich

Python validiert Ergebnisse *und* Notifications per `TypeAdapter`
(`RpcResponseValidationError`, `RpcNotificationValidationError`). TS castet
blind (`as Promise<Result>`) und nutzt bei Notifications nicht einmal die
Route-Konstante, sondern rohe Strings:

```ts
return this.rpc.notifications<TaskUpdated>("tasks.updated", "production");
```

Mindestens: Notifications über `routes`/`notifications`-Konstanten führen (ein
Tippfehler ist heute nicht typgeprüft). Optional: dokumentieren, dass TS
absichtlich nicht validiert — dann aber konsistent, inkl. Hinweis in der README.

### 2.8 Kleinkram

- TS-Client hat kein `[Symbol.asyncDispose]`, obwohl die Streams eines haben →
  `await using client = await Client.connect()` funktioniert nicht.
- `screencast(variables, options?)`: `variables` ist Pflicht-Argument, obwohl
  jedes Feld optional ist (`client.screencast({})`).
- Python-Routen sind Modulkonstanten (`TASKS_LIST`), TS ein `routes`-Objekt —
  rein kosmetisch, aber ohne Grund unterschiedlich.
- Kein Reconnect-/Retry-Konzept in beiden Clients. Das ist legitim, sollte aber
  explizit in der generierten README stehen, inklusive „Fehler nach Socket-Abriss
  = `RpcConnectionClosed`, neu verbinden liegt beim Aufrufer".

---

## 3. Zielbild

Leitgedanke: **Ein Import, ein Connect, alles andere hängt am Namespace-Baum.**
Server und Sockets sind Deployment-Details und tauchen im Alltagspfad nicht auf.

### TypeScript

```ts
import { AutomationClient } from "@acme/automation-client";

// 1. Häufigster Fall: alles auf einem Host, lazy verbunden.
await using client = await AutomationClient.connect({ host: "stage.acme.dev" });

// 2. Abweichende Deployments: nur überschreiben, was abweicht.
await using client = await AutomationClient.connect({
  host: "stage.acme.dev",
  servers: { browser: "wss://browser.stage.acme.dev/browser/rpc" },
  auth: { bearer: token },          // -> Header bzw. Subprotocol, pro Socket
  requestTimeoutMs: 10_000,
  signal: controller.signal,
});

const tasks = await client.tasks.list();
const tab = await client.browser.tabs.open({ url: "https://example.com" });

// Notifications
for await (const { task } of client.tasks.updated()) { ... }

// Binary-Stream: am Namespace, erbt Host/Auth vom Connect
await using frames = await client.browser.screencast.frames();
for await (const frame of frames) { render(frame); }

// Convenience: start() + Stream in einem Schritt
await using session = await client.browser.screencast.session({ quality: 80 });
for await (const frame of session) { render(frame); }
```

- `connect()` nimmt **eine** Options-Struktur; `host` gilt für alle Server, deren
  URL-Vorlage eine `host`-Variable hat. Heißt die Variable anders, erscheint sie
  unter ihrem Namen. Alles ist über `servers` punktuell überschreibbar.
- Sockets werden **lazy** beim ersten Request auf den jeweiligen Server geöffnet;
  `connect({ eager: true })` erzwingt paralleles Vorwärmen.
- Testpfad bleibt: `AutomationClient.withTransports({ production: fake, ... })`.
- `index.ts` exportiert Client, Namespace-Klassen, Modelle, Routen, alle Fehler
  und die Transport-Bausteine.

### Python

```python
from automation_client import AutomationClient, RpcRemoteError, Task

async with AutomationClient.connect(host="stage.acme.dev") as client:
    tasks: list[Task] = await client.tasks.list()
    tab = await client.browser.tabs.open(url="https://example.com")

    async for update in client.tasks.updated():
        ...

    async with client.browser.screencast.frames() as frames:
        async for frame in frames:
            ...

    async with client.browser.screencast.session(quality=80) as frames:
        ...
```

- `connect()` bleibt Async-Context-Manager, aber `__await__` wird entfernt oder
  liefert einen geöffneten Client — kein Objekt, das beides halb kann.
- Overrides als Keyword statt varargs:
  `connect(host=..., servers={ServerName.BROWSER: "wss://..."}, request_timeout=5)`.
- Optionale Parameter nutzen `UNSET`, sodass Server-Defaults gelten.
- `__init__.py` re-exportiert `models` (`Task`, `Tab`, …) und die
  Namespace-Klassen.

### Gemeinsamer Vertrag beider Sprachen

| Thema | Regel |
|---|---|
| Einstieg | `connect()` (Alltag) + `with_transports()` (Test) — mehr nicht |
| Server | Nie im Aufrufpfad sichtbar; Routing kommt aus dem Contract |
| Host/Variablen | Einmal beim Connect gesetzt, gilt für RPC **und** Streams |
| Optionale Params | Nicht gesetzt = nicht gesendet, in beiden Sprachen |
| Streams | Hängen am Namespace, sind Context-Manager, enden regulär |
| Fehler | Gleiche Hierarchie und gleiche Namen in beiden Sprachen, exportiert |
| Lebenszyklus | `async with` / `await using`, `close()` idempotent |

---

## 4. Ableitung für den Generator

1. `errors`-Modul unbedingt immer erzeugen (P0, Abschnitt 1.1), plus
   Import-/`tsc`-Smoke-Test über den generierten Beispiel-Output.
2. `UNSET`-Pfad in `python.py` für optionale Params verdrahten (1.3).
3. Pump-Endzustand in beiden Runtime-Cores speichern (1.4).
4. `connect`: lazy bzw. parallel, gemeinsame Variablenauflösung für Server und
   Streams, Options-Objekt statt varargs, Durchreichen von Socket-Factory und
   Queue-Größen (1.5, 1.6, 2.3).
5. Streams in Python über ein `resolve_stream_endpoint`-Äquivalent auflösen
   (1.7), `BinaryStreamOpening.__await__` streichen (1.8), reguläres Streamende
   (1.9).
6. Stream-Platzierung: Fixture auf `browser.screencast.frames` umstellen,
   Kollisionsprüfung Stream vs. Namespace-Knoten in `names.py` ergänzen (2.1).
7. Exportflächen angleichen: TS exportiert Namespaces/Errors/Routen, Python
   exportiert Modelle (2.5, 2.6).
8. Konstruktionswege reduzieren, `hooks` durchreichen (2.2, 1.10).
9. Optional, danach: `session()`-Convenience für Streams mit zugehöriger
   `start`-Methode — braucht eine Contract-Erweiterung, die den Stream mit
   seiner Startmethode verknüpft (z. B. `x-rpckit-binary-streams[].startMethod`).
