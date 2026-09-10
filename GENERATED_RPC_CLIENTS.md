# Zielbild für generierte RPC-Clients

Dieses Dokument beschreibt das angestrebte öffentliche API der generierten
Python- und TypeScript-Clients. Es ist zunächst eine Designgrundlage und keine
Beschreibung des bereits vollständig implementierten Verhaltens.

## Ziele

Ein generierter Client soll sich ähnlich bequem wie ein guter OpenAPI-Client
benutzen lassen, dabei aber die Eigenschaften bidirektionaler JSON-RPC-
Verbindungen abbilden:

- RPC-Namespaces werden als navigierbare, typisierte Client-API generiert.
- Requests, Responses, deklarierte Fehler und Notifications sind typisiert.
- Server-URLs und URL-Variablen stammen aus dem OpenRPC-Dokument.
- Die Zuordnung einer Route oder Notification zu einem Server bleibt für den
  Benutzer unsichtbar.
- Ein WebSocket-Transport kann auf Wunsch mitgeneriert werden. Im normalen Fall
  muss der Benutzer weder einen Transport schreiben noch URLs zusammensetzen.
- Eigene Transports bleiben für Tests, alternative WebSocket-Implementierungen
  und andere Übertragungswege möglich.
- Fachliche API, Deployment-Endpunkte und Transportimplementierung bleiben
  getrennte Konzepte.

HTTP-spezifisches Client-Tooling ist ausdrücklich nicht das Hauptziel. Für
klassische HTTP-APIs existiert mit OpenAPI bereits ein großes Ökosystem. Der
Schwerpunkt von `pyrpckit` liegt auf langlebigen, bidirektionalen Verbindungen,
insbesondere WebSockets mit Requests, Responses und serverseitigen
Notifications über dieselbe Verbindung.

## Contract

Deployment- und Transportinformationen werden am OpenRPC-Server beschrieben:

```python
from pyrpckit import OpenRpcContract, OpenRpcServer, ServerVariable


CONTRACT = OpenRpcContract(
    app=app,
    title="Automation",
    servers=(
        OpenRpcServer(
            name="production",
            url="wss://{host}/automation/rpc",
            variables={
                "host": ServerVariable(default="api.example.com"),
            },
            extensions={
                "x-rpckit-transport": {
                    "type": "websocket",
                    "messageEncoding": "json",
                }
            },
        ),
    ),
)
```

`x-rpckit-transport` soll durchgängig ein strukturiertes Objekt sein, kein
String. Die Generator-IR, die generierten Typen und die Validierung müssen
dasselbe Modell verwenden. Ein später erweiterbares Zielmodell ist:

```typescript
type RpcTransportDescriptor =
  | {
      type: "websocket";
      messageEncoding: "json";
      subprotocols?: readonly string[];
    }
  | {
      type: string;
      [option: string]: unknown;
    };
```

Ein `RpcRouter.server` ordnet fachliche Routen und Notifications einem
Deployment-Endpunkt zu. Der Servername wird niemals Teil der fachlichen
Client-API.

## Generatoroption für WebSockets

Ohne Transportoption bleibt der heutige transportagnostische Modus erhalten:

```bash
pyrpckit generate schema/automation.openrpc.json \
  --language typescript \
  --output src/generated
```

Der Consumer injiziert in diesem Modus einen eigenen `RpcTransport`.

Mit einem expliziten Flag wird zusätzlich ein einsatzbereiter
WebSocket-Transport erzeugt:

```bash
pyrpckit generate schema/automation.openrpc.json \
  --language typescript \
  --output src/generated \
  --with-transport websocket
```

Für Python gilt dieselbe Option:

```bash
pyrpckit generate schema/automation.openrpc.json \
  --language python \
  --package automation_client \
  --output src/automation_client \
  --with-transport websocket
```

`--with-transport websocket` bedeutet:

- Der Generator erzeugt `WebSocketTransport` und den notwendigen
  Verbindungsaufbau im Client-Paket.
- `Client.connect()` liest die generierten Serverdefinitionen und baut die
  benötigten Verbindungen automatisch auf.
- Standardwerte für URL-Variablen werden aus dem Contract übernommen.
- Mehrere Server erzeugen automatisch mehrere Verbindungen.
- Routes und Notifications werden anhand ihrer Serverzuordnung intern an die
  richtige Verbindung geleitet.
- Der generierte Transport implementiert JSON-RPC-Framing, Request-IDs,
  Response-Korrelation, parallele Requests, Notifications und geordnetes
  Schließen.

Der öffentliche Name lautet neutral `WebSocketTransport`, nicht
`BrowserWebSocketTransport`. Die fachliche Abstraktion ist ein WebSocket. Eine
konkrete WebSocket-Implementierung kann intern aus der Laufzeitumgebung stammen
oder optional injiziert werden.

Für TypeScript soll der Standardtransport eine verfügbare standardkompatible
`WebSocket`-Implementierung verwenden. Für Umgebungen ohne globale
Implementierung kann eine kompatible Constructor-/Socket-Factory übergeben
werden. Für Python darf der aktiv gewählte WebSocket-Modus eine klar
dokumentierte optionale Runtime-Abhängigkeit verwenden; ohne Flag bleibt das
generierte Paket frei von dieser Abhängigkeit.

## Trennung der öffentlichen Konzepte

Diese drei Dimensionen dürfen nicht miteinander vermischt werden:

| Ebene | Beispiel | Aufgabe |
| --- | --- | --- |
| Namespace | `client.tasks.status` | Fachliche Navigation |
| Route | `tasks.status.set` | JSON-RPC-Wire-Name |
| Server | `production` | Auswahl der Verbindung |

Der Consumer arbeitet normalerweise nur mit Namespaces. Route und Server sind
generierte Dispatch-Informationen.

## TypeScript-Zielbild

### Ein Server mit generiertem WebSocket-Transport

Sind alle Servervariablen mit Defaults belegt, ist kein Connection-Boilerplate
nötig:

```typescript
import { AutomationClient } from "@example/automation-client";

const client = await AutomationClient.connect();

const tasks = await client.tasks.list();

const created = await client.tasks.create({
  title: "Dokumentation schreiben",
});

await client.tasks.status.set({
  taskId: created.id,
  status: "done",
});

await client.close();
```

Wo Explicit Resource Management verfügbar ist, kann der Client zusätzlich
`AsyncDisposable` unterstützen:

```typescript
await using client = await AutomationClient.connect();

await client.tasks.list();
```

### Servervariablen überschreiben

Nur Werte, die von den Contract-Defaults abweichen, werden angegeben:

```typescript
const client = await AutomationClient.connect({
  endpoints: [
    endpoints.production({
      host: "staging.example.com",
    }),
  ],
});
```

Die Endpoint-Factory trägt den Servernamen bereits als typisierte Identität.
Dadurch gibt es im normalen API weder einen rohen Servernamen noch ein frei
geformtes Variablen-Dictionary. Variablennamen und gegebenenfalls Enum-Werte
sind statisch typisiert. Unbekannte Werte sollen bereits beim TypeScript-Check
scheitern. Nicht angegebene Server verwenden weiterhin ihre generierten
Defaults.

### Mehrere Server

Auch bei mehreren Servern bleibt der Standardfall kurz:

```typescript
const client = await AutomationClient.connect();

await client.tasks.list();
await client.browser.tabs.open({ url: "https://example.com" });
await client.browser.screencast.start({ quality: 80 });
```

Der Client kann intern beispielsweise folgende Zuordnung besitzen:

```text
tasks.*                 -> production
browser.tabs.*          -> browser
browser.screencast.*    -> streaming
```

Für abweichende Deployments werden lediglich Variablen überschrieben:

```typescript
const client = await AutomationClient.connect({
  endpoints: [
    endpoints.production({ host: "api.example.com" }),
    endpoints.browser({ host: "browser.example.com" }),
    endpoints.streaming({ host: "stream.example.com" }),
  ],
});
```

Der Consumer soll nicht die bereits im Contract vorhandene Route-zu-Server-
Zuordnung als `connections`-Objekt wiederholen müssen.

### Namespaces und Routen

Punktgetrennte RPC-Namen werden vorhersehbar als Objektbaum abgebildet:

```text
tasks.list                  -> client.tasks.list()
tasks.create                -> client.tasks.create()
tasks.status.set            -> client.tasks.status.set()
users.get                   -> client.users.get()
users.permissions.list      -> client.users.permissions.list()
browser.tabs.open           -> client.browser.tabs.open()
browser.screencast.start    -> client.browser.screencast.start()
```

Die generierten Namespace-Klassen und Dateien sind Implementierungsdetails.
Öffentlich relevant ist allein die navigierbare API:

```typescript
await client.users.permissions.list({ userId: "456" });
```

`api_root` und explizite API-Namensabbildungen bleiben verfügbar, um einen
technischen gemeinsamen Präfix auszublenden oder öffentliche Namen anzupassen,
ohne die JSON-RPC-Wire-Namen zu verändern.

### Notifications

Notifications erhalten einen eigenen, ebenfalls namespaced Einstiegspunkt.
Damit ist am Aufrufort sichtbar, ob eine Operation einen Request sendet oder
einen Ereignisstrom öffnet:

```typescript
for await (const update of client.events.tasks.updated()) {
  console.log(update);
}

for await (const frame of client.events.browser.screencast.frame()) {
  render(frame);
}
```

Die Zuordnung der Notification zum Server wird generiert. Der Runtime-Core muss
pro Verbindung genau einen eingehenden Nachrichtenstrom lesen und die
Notifications anschließend verteilen. Mehrere Consumer dürfen nicht direkt
konkurrierend aus demselben WebSocket lesen.

### Eigener Transport als Escape Hatch

Dependency Injection bleibt möglich:

```typescript
const client = AutomationClient.fromTransport(testTransport);
```

`testTransport` ist hier ein vom Test oder von der Anwendung bereitgestelltes
Objekt, das `RpcTransport` implementiert. Es gibt keinen vorausgesetzten oder
magischen `handler`. Ein In-Memory-Adapter darf als separates Testwerkzeug einen
lokalen Dispatcher akzeptieren, ist aber nicht Bestandteil der generierten
fachlichen API.

Für mehrere benutzerdefinierte Verbindungen kann eine ausführliche Low-Level-API
angeboten werden:

```typescript
const client = AutomationClient.fromTransports({
  production: productionTransport,
  browser: browserTransport,
  streaming: streamingTransport,
});
```

Das Argument ist kein beliebiges `Record<string, RpcTransport>`, sondern ein
contract-spezifisch generierter Typ:

```typescript
type AutomationTransports = {
  readonly production: RpcTransport;
  readonly browser: RpcTransport;
  readonly streaming: RpcTransport;
};

type ServerName = keyof AutomationTransports;
```

Damit bietet die IDE die gültigen Keys an, fehlende benötigte Verbindungen und
zusätzliche Keys werden vom TypeScript-Compiler beanstandet. Ein TypeScript-
`enum` bringt hier gegenüber dem exakt typisierten Objekt keinen Vorteil. Für
dynamischen Code kann zusätzlich `ServerName` als String-Union und
`servers.production.name` als typisierter Laufzeitwert exportiert werden.

Diese Form ist für Tests und Spezialfälle gedacht. Sie ist nicht der normale
Quickstart.

## Python-Zielbild

### Ein Server mit generiertem WebSocket-Transport

Der bevorzugte Python-Lebenszyklus nutzt einen asynchronen Context Manager:

```python
from automation_client import AutomationClient


async with AutomationClient.connect() as client:
    tasks = await client.tasks.list()

    created = await client.tasks.create(
        title="Dokumentation schreiben",
    )

    await client.tasks.status.set(
        task_id=created.id,
        status="done",
    )
```

`connect()` liefert in diesem Modell einen asynchronen Connection-Context. Für
Anwendungen mit separat verwaltetem Lebenszyklus kann eine explizite Variante
ergänzt werden:

```python
client = await AutomationClient.open()
try:
    await client.tasks.list()
finally:
    await client.close()
```

Damit wird die unhandliche Form
`async with await AutomationClient.connect()` vermieden.

### Servervariablen überschreiben

In der normalen Python-API werden keine verschachtelten String-Dictionaries
verwendet. Der Generator stellt pro Server eine typisierte Endpoint-Factory
bereit:

```python
from automation_client import AutomationClient, endpoints


async with AutomationClient.connect(
    endpoints.production(host="staging.example.com")
) as client:
    await client.tasks.list()
```

`production()` besitzt eine generierte Signatur. Dadurch vervollständigen IDE
und Typechecker `host`, prüfen erforderliche Variablen und können Enum-Werte als
`Literal` darstellen. Das Ergebnis trägt außerdem die Identität des Servers;
der Aufrufer muss `"production"` nicht ein zweites Mal als String angeben.
Nicht übergebene Server verwenden ihre Contract-Defaults.

Für APIs, die Servernamen dynamisch behandeln müssen, wird ergänzend ein
generiertes String-Enum angeboten:

```python
from enum import StrEnum


class ServerName(StrEnum):
    PRODUCTION = "production"
    BROWSER = "browser"
    STREAMING = "streaming"
```

Das Enum ist für Low-Level- und dynamische APIs gedacht. Im normalen
`connect()`-Aufruf sind die Endpoint-Factories ergonomischer.

### Mehrere Server

Die Server- und Routenverkabelung bleibt auch in Python generiert:

```python
async with AutomationClient.connect() as client:
    await client.tasks.list()
    await client.browser.tabs.open(url="https://example.com")
    await client.browser.screencast.start(quality=80)
```

Nur abweichende Werte werden konfiguriert:

```python
async with AutomationClient.connect(
    endpoints.production(host="api.example.com"),
    endpoints.browser(host="browser.example.com"),
    endpoints.streaming(host="stream.example.com"),
) as client:
    await client.tasks.list()
```

### Notifications

Notifications folgen demselben Pfadmodell wie im TypeScript-Client:

```python
async for update in client.events.tasks.updated():
    print(update)

async for frame in client.events.browser.screencast.frame():
    render(frame)
```

Payloads werden vor dem Yield mit den generierten Pydantic-Modellen validiert.

### Eigener Transport als Escape Hatch

Der transportagnostische Einstieg bleibt erhalten:

```python
async with AutomationClient.from_transport(test_transport) as client:
    await client.tasks.list()
```

`test_transport` ist ein normales Objekt, das das `RpcTransport`-Protocol
implementiert. Falls ein In-Memory-Transport angeboten wird, definiert dessen
separate API, ob er einen lokalen Dispatcher oder eine Map aus Testantworten
annimmt. Der generierte Client setzt keinen unklaren `handler` voraus.

Für mehrere selbst verwaltete Transports:

```python
async with AutomationClient.from_transports(
    production=production_transport,
    browser=browser_transport,
    streaming=streaming_transport,
) as client:
    await client.tasks.list()
```

Die Keyword-Parameter werden contract-spezifisch generiert. Dadurch sind die
gültigen Servernamen in der IDE sichtbar und Tippfehler werden von Python selbst
beziehungsweise vom Typechecker erkannt. Für dynamisch zusammengesetzte Maps
kann eine separate Low-Level-Funktion das generierte Enum verwenden:

```python
async with AutomationClient.from_transport_map(
    {
        ServerName.PRODUCTION: production_transport,
        ServerName.BROWSER: browser_transport,
        ServerName.STREAMING: streaming_transport,
    }
) as client:
    await client.tasks.list()
```

## Generierte Dateien

Mit WebSocket-Unterstützung könnte ein TypeScript-Client folgende Struktur
haben:

```text
generated/
  index.ts
  client.ts
  core.ts
  transport.ts
  endpoints.ts
  routes.ts
  events.ts
  errors.ts
  models.ts
  namespaces/
    tasks/
    users/
    browser/
```

Für Python entsprechend:

```text
automation_client/
  __init__.py
  client.py
  core.py
  transport.py
  endpoints.py
  routes.py
  events.py
  errors.py
  models.py
  namespaces/
    tasks/
    users/
    browser/
```

`transport.ts` beziehungsweise `transport.py` wird nur mit
`--with-transport websocket` erzeugt. Ohne Flag importiert oder akzeptiert der
Client weiterhin ausschließlich das kleine `RpcTransport`-Protokoll.

## Laufzeitrelevante Routeninformationen

Die Route-Tabelle sollte ausschließlich Informationen enthalten, die der Core
für Dispatch und Fehlerbehandlung benötigt:

```typescript
export const routes = {
  tasksList: {
    method: "tasks.list",
    server: "production",
  },
  browserTabsOpen: {
    method: "browser.tabs.open",
    server: "browser",
  },
  browserScreencastStart: {
    method: "browser.screencast.start",
    server: "streaming",
  },
} as const;
```

`summary`, `tags`, ausführliche Fehlerbeschreibungen und ähnliche
Dokumentationsinformationen gehören nicht zwingend in jedes Runtime-Bundle. Sie
können bei Bedarf separat als Reflection-/Dokumentationsartefakt erzeugt werden.

Da das aktuelle Protokoll einen Router genau einem Server zuordnet, ist ein
einzelnes optionales Feld `server` klarer als `serverNames`. Falls zukünftig eine
Route mehrere alternative Server unterstützen soll, braucht diese Auswahl eine
explizite Semantik; ein bloßes Array ohne Auswahlregel reicht dafür nicht.

## WebSocket-Verhalten

Der mitgenerierte Standardtransport benötigt definierte Semantik für mindestens
folgende Punkte:

- Vergabe kollisionsfreier JSON-RPC-Request-IDs
- Korrelation paralleler Responses
- Trennung von Responses und Notifications
- Fehler für unbekannte oder doppelte Response-IDs
- Verhalten bei fehlerhaftem JSON und ungültigen JSON-RPC-Nachrichten
- sauberes Schließen und Ablehnen noch offener Requests
- optionaler Request-Timeout
- Backpressure beziehungsweise begrenzte Notification-Queues
- Reconnect-Verhalten

Ein Reconnect darf offene mutierende Requests nicht automatisch erneut senden.
Ob ein Request den Server erreicht hat, ist nach einem Verbindungsabbruch nicht
allgemein feststellbar. Ein Standardadapter sollte offene Requests daher
ablehnen; automatische Wiederholungen benötigen eine explizite, fachlich
vertretbare Policy.

Authentifizierung ist umgebungsabhängig. Browser-WebSockets erlauben keine frei
wählbaren HTTP-Header, während andere Implementierungen dies können. Das
generierte öffentliche API darf deshalb Authentifizierung nicht ausschließlich
als `headers` modellieren. URL-Variablen, Cookies, Subprotokolle und injizierte
Socket-Factories müssen möglich bleiben.

## Festgehaltene Designentscheidungen

1. Der normale Consumer schreibt keinen `connections`-Boilerplate.
2. Serverdefinitionen und Route-zu-Server-Zuordnungen werden vollständig aus
   OpenRPC generiert.
3. `--with-transport websocket` erzeugt einen direkt nutzbaren Standardtransport.
4. Der neutrale öffentliche Name ist `WebSocketTransport`.
5. Namespaces bilden ausschließlich die fachliche API ab; Server bleiben intern.
6. Notifications liegen unter einem separaten, typisierten `events`-Baum.
7. Eigene Transports und explizite Transport-Maps bleiben als Low-Level-Escape-
   Hatch erhalten.
8. Runtime-Routenmetadaten werden auf tatsächlich verwendete Informationen
   reduziert; Dokumentationsmetadaten werden davon getrennt.
9. Endpoint-Overrides werden über generierte Factory-Funktionen statt über
   verschachtelte String-Dictionaries angegeben.
10. Python exportiert für dynamische Low-Level-Verwendung ein `ServerName`-
    `StrEnum`; die ergonomische API verwendet generierte Endpoint-Factories und
    Keyword-Parameter.
11. TypeScript verwendet exakt typisierte Objekt-Keys und eine `ServerName`-
    String-Union statt eines Runtime-Enums.

## Noch zu entscheidende Details

- Ob `--with-transport websocket` exakt dieser CLI-Name wird oder in der
  Konfigurationsdatei zusätzlich eine transportbezogene Zieloption erhält.
- Welche konkrete optionale WebSocket-Abhängigkeit der Python-Generator nutzt.
- Wie TypeScript-Projekte ohne globale `WebSocket`-Implementierung die
  Standardimplementierung konfigurieren.
- Ob Timeouts Teil des allgemeinen `RpcTransport`-Protokolls oder ausschließlich
  Client-/Adapteroptionen sind.
- Welche Queue- und Overflow-Policy für langsame Notification-Consumer gilt.
- Wie Namenskollisionen zwischen Contract-Namespaces und reservierten
  Client-Eigenschaften wie `events` und `close` behandelt werden.
