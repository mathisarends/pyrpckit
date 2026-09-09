# Client Generation

## Inhaltsverzeichnis

- [Status und Ziel](#status-und-ziel)
- [Entscheidung in Kurzform](#entscheidung-in-kurzform)
- [Was schöner generierter Code bedeutet](#was-schöner-generierter-code-konkret-bedeutet)
  - [Anti-Stutter und API-Root](#anti-stutter-durch-einen-expliziten-api-root)
- [Begriffe](#begriffe)
- [Quellen der generierten API](#quellen-der-generierten-api)
- [Öffentliche Python-API](#öffentliche-python-api)
- [Python-Paketstruktur](#struktur-eines-generierten-packages)
  - [Ein Contract](#ein-contract)
  - [Mehrere Clients](#mehrere-clients-in-einem-consumer-repository)
  - [Mehrere Server](#ein-contract-mit-mehreren-servern)
- [Beispiel des generierten Python-Codes](#beispiel-des-generierten-python-codes)
- [Benennungsregeln](#benennungsregeln)
- [Parameter- und Ergebnismodell](#parameter--und-ergebnismodell)
- [Routenmetadaten und Client-Hooks](#routenmetadaten-und-client-hooks)
- [TypeScript-Client](#typescript-client)
  - [Delta zum heutigen Generator](#delta-zum-heutigen-typescript-generator)
  - [Öffentliche TypeScript-API](#öffentliche-typescript-api)
  - [TypeScript-Ordnerstruktur](#typescript-ordnerstruktur)
  - [Generierte TypeScript-Dateien](#generierte-typescript-dateien)
  - [TypeScript-Typen und Wire-Semantik](#typescript-typen-und-wire-semantik)
- [Mehrere Transports und methodenspezifische Server](#mehrere-transports-und-methodenspezifische-server)
- [Generator-Konfiguration](#generator-konfiguration)
- [Sprachneutrales IR](#notwendige-erweiterungen-des-sprachneutralen-ir)
- [Determinismus und Qualität](#determinismus-und-qualität)
- [Migration](#rückwärtskompatibilität-und-migration)
- [Nicht empfohlen](#nicht-empfohlen)
- [Akzeptanzkriterien](#akzeptanzkriterien)
- [Referenzen](#referenzen)

## Status und Ziel

Dieses Dokument konkretisiert die Client-Generierung aus
[`SPEC.md`](SPEC.md). Es beschreibt die gewünschte öffentliche Python- und
TypeScript-API, die generierten Paketstrukturen und die Abbildung der neuen
Begriffe `RpcRouter`, `RpcRoute`, `RpcApp`, Notification und `OpenRpcContract` auf
Clients.

Der wichtigste Grundsatz lautet:

> Ein OpenRPC-Contract erzeugt genau einen Root-Client. Seine RPC-Routen bilden
> darunter einen hierarchischen API-Baum. Mehrere Contracts erzeugen mehrere
> voneinander unabhängige Client-Packages.

Die Generatoren lesen weiterhin ausschließlich das OpenRPC-Dokument. Sie lesen
weder `RpcApp` noch Python-Handler oder `RpcProtocol`. Damit bleibt ein Contract
die Sprach- und Repository-Grenze.

Dieses Dokument ist zunächst eine Zielspezifikation. Es beschreibt bewusst auch
Änderungen gegenüber dem derzeitigen Generator.

## Entscheidung in Kurzform

- Der Root-Typ heißt `<Contract>Client`, beispielsweise
  `BrowserControlClient`. Nur dieser Typ besitzt Transport und Lifecycle.

- Untergeordnete Gruppen heißen `<Route>Api`, beispielsweise `BrowserNavApi`.
  Der heutige Name `NamespaceClient` entfällt aus der öffentlichen API.

- Der Aufruf folgt standardmäßig dem vollständigen, Punkt-separierten
  Wire-Namen: `browser.nav.navigate` wird zu
  `client.browser.nav.navigate(...)`. Ein explizit konfigurierter gemeinsamer
  Root-Prefix darf für eine schönere, nicht stotternde API entfernt werden.

- Router-Tags bestimmen nicht den Attributbaum. Sie sind nicht eindeutig, eine
  Route darf mehrere Tags besitzen und Include-Tags können ergänzt werden.
  Tags bleiben deshalb Dokumentations- und Instrumentierungsmetadaten.

- Der Begriff **Notification** wird durchgängig von der Protokolldeklaration bis
  zur Consumer-API verwendet. Der typisierte Stream heißt deshalb
  `client.notifications()`.

- `OpenRpcContract.servers` erzeugt Endpoint-Metadaten, aber keine zusätzlichen
  Client-Klassen. Ein Server ist eine Deployment-Alternative derselben API.

- Jeder Contract wird in ein eigenes Leaf-Package geschrieben. Gemeinsam
  genutzt wird nur die handgeschriebene Runtime aus `pyrpckit.client`; Modelle
  oder API-Gruppen verschiedener Contracts werden standardmäßig nicht
  zusammengeführt.

- Öffentliche Operationsmethoden verwenden Python-konforme `snake_case`-Namen
  und keyword-only Parameter. Der originale Wire-Name bleibt unverändert in
  den Routenmetadaten erhalten.

- Der TypeScript-Emitter verwendet `camelCase`, ein benanntes Parameterobjekt
  und dieselbe Route-Hierarchie. Er kopiert keine Python-Syntax.

- Nicht gesetzt und explizit `None` sind verschiedene Zustände. Optionale
  Parameter verwenden deshalb ein `UNSET`-Sentinel; sie dürfen nicht pauschal
  mit `exclude_none=True` verschwinden.

## Was „schöner generierter Code“ konkret bedeutet

Der generierte Client soll sich am Call-Site wie eine kleine handgeschriebene
Python-Library anfühlen. Schönheit ist hier kein dekoratives Ziel, sondern eine
Kombination aus Lesbarkeit, Vorhersagbarkeit und wenig sichtbarer Infrastruktur.

Eine gute Call-Site sieht so aus:

```python
async with BrowserControlClient(transport) as browser:
    await browser.navigation.navigate(url="https://example.com")
    tabs = await browser.tabs.list()
```

Nicht so:

```python
async with BackendBrowserTunnelApiClient(transport) as api_client:
    params = BrowserNavNavigateRequestParamsModel(url="https://example.com")
    await api_client.browser_namespace_client.nav_namespace_client.navigate(params)
```

Daraus folgen diese Gestaltungsregeln:

- Der Root-Client hat einen kurzen Domain-Namen und genau eine Aufgabe.

- Der Contractname wird nicht in jedem Kindobjekt wiederholt.

- Der Consumer sieht Domain-Gruppen wie `navigation` und `tabs`, nicht
  Generatorbegriffe wie `namespace`, `service_proxy` oder `api_client`.

- Einfache Parameter werden direkt als keyword-only Argumente angeboten. Ein
  Request-Wrapper ist an der Call-Site nur sichtbar, wenn das Schema selbst ein
  fachliches verschachteltes Objekt enthält.

- Rückgaben sind konkrete Domain-Modelle. `dict[str, Any]`, generische
  `Response`-Wrapper und Casts tauchen nicht in normalem Consumer-Code auf.

- RPC-Wire-Namen, Error-Codes und Serialisierungsdetails bleiben im generierten
  Inneren, sind aber für Debugging und Instrumentierung über Metadaten
  erreichbar.

- Generierte Dateien besitzen kleine, nachvollziehbare Verantwortlichkeiten,
  minimale Imports und normale Ruff-formatierte Python-Syntax. Sie sollen beim
  Debuggen lesbar sein, obwohl sie nicht editiert werden.

- Das Top-Level-Package exportiert nur die Dinge, die ein normaler Consumer
  tatsächlich braucht.

### Anti-Stutter durch einen expliziten API-Root

Der vollständige Wire-Name bleibt die einzig sichere Defaultquelle für den
API-Baum. Bei einem Contract, dessen Methoden fast alle mit `browser.` beginnen,
ist `BrowserControlClient.browser...` aber unnötige Wiederholung. Die
Python-Generatoroptionen erhalten deshalb einen expliziten `api_root`:

```python
PythonClientOptions(
    package="backend.generated.rpc.browser_control",
    client_name="BrowserControlClient",
    api_root="browser",
    api_names={
        "nav": "navigation",
        "tab": "tabs",
    },
)
```

Damit gilt:

```text
browser.nav.navigate -> browser.navigation.navigate(...)
browser.tab.list      -> browser.tabs.list()
```

Hier bezeichnet das linke `browser` den Wire-Prefix und das rechte `browser`
die lokale Variable des Consumers:

```python
async with BrowserControlClient(transport) as browser:
    await browser.navigation.navigate(url="https://example.com")
    tabs = await browser.tabs.list()
```

Die Option ist bewusst explizit. Ein automatisch erkannter gemeinsamer Prefix
könnte die gesamte öffentliche API verschieben, sobald später eine neue
Root-Route hinzukommt. `api_root` verändert nur Python-Zugriffspfade, niemals den
Wire-Namen.

`api_names` ist ebenfalls eine bewusste Consumer-Namensabbildung. Sie wird auf
vollständige API-Pfade erweitert, sobald zwei gleichnamige Segmente
unterschiedlich übersetzt werden müssen, beispielsweise
`{"browser.nav": "navigation"}`. Jede Abbildung wird gegen den Contract
validiert; unbekannte Pfade und daraus entstehende Kollisionen sind Fehler.

## Begriffe

Die Begriffe sollen in Dokumentation, generiertem Code und Fehlermeldungen
einheitlich verwendet werden.

| Begriff | Bedeutung | Beispiel |
| --- | --- | --- |
| Contract | Ein vollständiges OpenRPC-Dokument und die Grenze eines generierten Packages | Browser Control |
| Client | Root-Objekt für genau einen Contract; besitzt Transport und Lifecycle | `BrowserControlClient` |
| API-Gruppe | Python-Objekt für einen Knoten im RPC-Namensbaum | `BrowserNavApi` |
| Operation | Aufrufbare Methode auf einer API-Gruppe | `navigate(...)` |
| RPC-Route | Unveränderliche Client-Metadaten einer Operation | `BROWSER_NAV_NAVIGATE` |
| Wire-Name | Wert des JSON-RPC-Felds `method` | `browser.nav.navigate` |
| Notification | Typisierte, serverinitiierte JSON-RPC-Nachricht ohne Request-ID | `BrowserUrlChanged` |
| Endpoint | Aufgelöste Adresse, an der ein Contract erreichbar ist | `wss://…/control` |
| Server | Benannte, möglicherweise templatisierte Endpoint-Beschreibung aus OpenRPC | `browser-control` |
| Transport | Überträgt JSON-RPC-Envelopes; HTTP, WebSocket, stdio oder in-memory | `WebSocketTransport` |

`ApiClient` sollte nicht als zweiter Root-Begriff eingeführt werden. Das führt
zu schwer unterscheidbaren Typen wie `BrowserControlApiClient` und
`BrowserNavClient`. Kanonisch ist:

```python
BrowserControlClient  # Contract und Lifecycle
BrowserApi  # Gruppe
BrowserNavApi  # Untergruppe
```

## Quellen der generierten API

### Normative Abbildung

| OpenRPC / rpckit | Generierter Python-Code |
| --- | --- |
| `info.title` | Default für den Root-Client-Namen |
| `info.version` | `CONTRACT_VERSION` |
| vollständiger Methodenname | Attributbaum, Operationsname und exakter Wire-Name |
| `methods[].params` | keyword-only Operationsparameter |
| `methods[].result.schema` | validierter Rückgabetyp |
| `methods[].summary` / `description` | Docstring der Operation |
| `methods[].deprecated` | Deprecation-Metadatum und optional Runtime-Warnung |
| `methods[].tags` | `RpcRouteInfo.tags`, niemals primäre Gruppierung |
| `methods[].errors` | deklarierte Remote-Fehler und Routenmetadaten |
| `x-rpc-notification-types` | Nachrichtentypen des Notification-Streams |
| `servers` | `endpoints.py` und Servervariablen |
| `x-rpckit-transport` | Transporthinweis in Endpoint-Metadaten |
| `x-rpc-protocol-version` | `PROTOCOL_VERSION` |

### Warum der Wire-Name die Hierarchie bestimmt

Ein Router kann mehrfach unter verschiedenen Prefixen inkludiert werden. Eine
Route kann außerdem mehrere Tags besitzen. Nur der materialisierte vollständige
Methodenname ist nach der Komposition eindeutig:

```text
primary.browser.nav.navigate
secondary.browser.nav.navigate
```

Daraus entstehen ohne zusätzliche Heuristik:

```python
await client.primary.browser.nav.navigate(url="https://example.com")
await client.secondary.browser.nav.navigate(url="https://example.com")
```

Tags wie `("browser", "control", "internal")` werden an beiden Operationen als
Metadaten erhalten, erzeugen aber keine weiteren Zugriffspfade.

### Root-Operationen

Ein Methodenname ohne Punkt bleibt direkt am Client:

```text
health -> await client.health()
```

Ein Methodenname mit Punkten wird segmentweise aufgebaut:

```text
browser.nav.navigate -> await client.browser.nav.navigate(...)
browser.tab.activate -> await client.browser.tab.activate(...)
session.close        -> await client.session.close()
```

Die bestehende Umsetzung mit `rpartition(".")` und einer Datei wie
`namespaces/browser.nav.py` ist nicht ausreichend. Ein Punkt in einem Dateinamen
ist keine Python-Paketstruktur und ein Import von
`package.namespaces.browser.nav` erwartet tatsächlich die Verzeichnisse
`browser/nav.py`.

## Öffentliche Python-API

### Normaler Aufruf

```python
from backend.generated.rpc.browser_control import BrowserControlClient
from backend.rpc_transport import WebSocketTransport


transport = WebSocketTransport(
    "wss://api.example.com/api/v1/projects/p-123/browser-tunnel/sessions/s-456/control"
)

async with BrowserControlClient(transport) as client:
    await client.navigation.navigate(url="https://example.com")
    tabs = await client.tabs.list()
    await client.tabs.activate(tab_id=tabs.items[0].id)
```

Die API ist absichtlich langweilig:

- Methoden sind asynchron, weil der zugrunde liegende Transport asynchron ist.

- Parameter sind keyword-only. Call-Sites bleiben lesbar und Änderungen an der
  Reihenfolge von Schemafeldern sind nicht API-brechend.

- Eingaben werden vor dem Senden und Ergebnisse nach dem Empfang validiert.

- Die API-Gruppe hält keine eigene Verbindung und besitzt kein `close()`.

- Der Root-Client ist ein Async Context Manager und bietet zusätzlich ein
  idempotentes `close()`.

### Ereignisse

```python
from backend.generated.rpc.browser_control.models import (
    BrowserTabClosed,
    BrowserUrlChanged,
)


async with BrowserControlClient(transport) as client:
    async for notification in client.notifications():
        match notification:
            case BrowserUrlChanged(url=url):
                print("new URL", url)
            case BrowserTabClosed(tab_id=tab_id):
                print("closed", tab_id)
```

`notifications()` validiert den vollständigen Notification-Envelope und liefert
den typisierten Notification-Payload beziehungsweise den bereits im Contract
definierten Notification-Message-Typ. Welche der beiden Formen gilt, muss der
OpenRPC-Extension eindeutig zu entnehmen sein; der Generator darf nicht anhand
von Feldnamen raten.

Das Low-Level-Transportinterface darf weiterhin
`transport.notifications()` heißen, weil dies der JSON-RPC-Begriff ist. Diese
Methode wird nicht am generierten Root-Client gespiegelt.

### Explizit `None` versus nicht gesetzt

Die heutige Form

```python
params.model_dump(mode="json", exclude_none=True)
```

ist semantisch falsch, sobald ein optionales Feld explizit `null` akzeptiert.
Der generierte Aufruf soll deshalb so aussehen:

```python
async def update_title(
    self,
    *,
    title: str | None | UnsetType = UNSET,
) -> Tab:
    values = {} if title is UNSET else {"title": title}
    params = UpdateTitleParams.model_validate(values)
    ...
```

Der Sentinel gelangt damit nie in Pydantic oder auf den Wire. Nur tatsächlich
übergebene Werte werden validiert und serialisiert:

```python
await client.tabs.update_title()  # Feld fehlt
await client.tabs.update_title(title=None)  # {"title": null}
await client.tabs.update_title(title="Docs")
```

Für einen optionalen, aber nicht nullable Parameter lautet die Annotation
entsprechend `str | UnsetType = UNSET`. Für ein required nullable Feld lautet sie
`str | None` ohne Default.

## Struktur eines generierten Packages

### Ein Contract

Für die Methoden

```text
health
browser.nav.navigate
browser.nav.reload
browser.tab.list
browser.tab.activate
session.close
```

wird mit der oben empfohlenen Konfiguration aus `api_root="browser"` und den
sprechenden Gruppenaliases folgendes Package erzeugt:

```text
browser_control/
├── __init__.py
├── client.py
├── endpoints.py
├── errors.py
├── metadata.py
├── models.py
└── api/
    ├── __init__.py
    ├── navigation.py
    ├── tabs.py
    └── session.py
```

Die Verantwortlichkeiten sind stabil und unabhängig von der Größe des
Contracts:

| Datei | Inhalt |
| --- | --- |
| `__init__.py` | kleiner, kuratierter Public Export |
| `client.py` | Root-Client, Lifecycle, Root-Operationen, Notification-Stream |
| `endpoints.py` | OpenRPC-Server, URL-Templates und Variablen |
| `errors.py` | contract-spezifische deklarierte Remote-Fehler |
| `metadata.py` | Contract- und Routenmetadaten, exakte Wire-Namen |
| `models.py` | Enums, Pydantic-Modelle und Typaliase |
| `api/…` | API-Baum und Operationsmethoden |

`endpoints.py` darf entfallen, wenn der Contract keine expliziten Server
enthält. `errors.py` bleibt für ein stabiles Layout bestehen, auch wenn noch
keine contract-spezifischen Fehler vorhanden sind.

`models.py` bleibt im ersten Schnitt bewusst eine Datei. Das verhindert
zirkuläre Imports und macht `model_rebuild()` deterministisch. Eine spätere
Aufteilung in ein `models/`-Package wäre eine reine Generatoroption und darf die
öffentlichen Imports aus `<package>.models` nicht ändern.

### Mehrere Clients in einem Consumer-Repository

Mehrere Contracts liegen als Geschwister unter einem handgeschriebenen
Eltern-Package:

```text
src/backend/generated/rpc/
├── __init__.py                 # handgeschrieben, nicht von einem Leaf-Run berührt
├── browser_control/            # aus browser-control.openrpc.json
│   ├── __init__.py
│   ├── client.py
│   ├── endpoints.py
│   ├── errors.py
│   ├── metadata.py
│   ├── models.py
│   └── api/...
├── browser_session/            # aus browser-session.openrpc.json
│   ├── __init__.py
│   ├── client.py
│   ├── endpoints.py
│   ├── errors.py
│   ├── metadata.py
│   ├── models.py
│   └── api/...
└── project_admin/              # aus project-admin.openrpc.json
    ├── __init__.py
    ├── client.py
    ├── endpoints.py
    ├── errors.py
    ├── metadata.py
    ├── models.py
    └── api/...
```

Verwendung:

```python
from backend.generated.rpc.browser_control import BrowserControlClient
from backend.generated.rpc.browser_session import BrowserSessionClient


async with BrowserSessionClient(session_transport) as sessions:
    session = await sessions.open(project_id=project_id)

async with BrowserControlClient(control_transport) as browser:
    await browser.navigation.navigate(url="https://example.com")
```

Jeder Generatorlauf besitzt ausschließlich sein Leaf-Verzeichnis. Er darf weder
das Eltern-`__init__.py` noch Dateien eines anderen Clients ändern oder löschen.

Modelle werden nicht automatisch über Contract-Grenzen dedupliziert. Zwei
Contracts können denselben Schemanamen mit anderer Version oder Semantik
besitzen. Automatisches Zusammenführen würde eine versteckte Kopplung erzeugen.
Explizit gemeinsam versionierte Modelle können später als separates
handgeschriebenes oder separat generiertes Package angeboten werden.

### Ein Contract mit mehreren Servern

Mehrere OpenRPC-Server erzeugen keine Ordner wie `production_client/` und
`local_client/`. Es bleibt ein Client:

```python
from backend.generated.rpc.browser_control import BrowserControlClient
from backend.generated.rpc.browser_control.endpoints import Servers


url = Servers.BROWSER_CONTROL.resolve(
    host="api.example.com",
    project_id=project_id,
    session_id=session_id,
)
client = BrowserControlClient(WebSocketTransport(url))
```

Das ist wichtig, weil `servers` Deployment-Metadaten und keine unterschiedlichen
APIs beschreiben. Erst zwei unterschiedliche OpenRPC-Dokumente sind zwei
unterschiedliche generierte Clients.

## Beispiel des generierten Python-Codes

Die folgenden Snippets sind exemplarisch, aber hinsichtlich Namen und
Verantwortlichkeiten normativ.

Innerhalb des generierten Python-Packages werden absolute Imports aus dem in
`PythonClientOptions.package` konfigurierten Package erzeugt. Dadurch ist in
jeder Datei unmittelbar sichtbar, woher ein Typ stammt, und die Ausgabe folgt
einer einheitlichen Importregel.

### `metadata.py`

```python
# Generated by pyrpckit from browser-control.openrpc.json.
# Do not edit this file manually.
from pyrpckit.client import RpcContractInfo, RpcRouteInfo


CONTRACT = RpcContractInfo(
    title="Backend Browser Tunnel",
    version="2.0.0",
    protocol_version=2,
)

BROWSER_NAV_NAVIGATE = RpcRouteInfo(
    method="browser.nav.navigate",
    summary="Navigate the active tab.",
    tags=("browser", "control"),
    deprecated=False,
    error_codes=(),
)

BROWSER_TAB_ACTIVATE = RpcRouteInfo(
    method="browser.tab.activate",
    summary="Activate a browser tab.",
    tags=("browser", "control"),
    deprecated=False,
    error_codes=(-32004,),
)
```

`RpcRouteInfo` ist eine unveränderliche Runtime-Dataclass. Sie ersetzt nicht den
String im JSON-RPC-Envelope, sondern hält die beim Generieren verfügbaren
Informationen zusammen. Der gemeinsame Client-Core gibt
`route.method` an `RpcTransport.request(...)` weiter und kann die übrigen Felder
für Error-Mapping, Telemetrie und Debugging verwenden.

Eine zusätzliche `RpcMethod(StrEnum)` kann aus Rückwärtskompatibilitätsgründen
zunächst exportiert werden. Langfristig ist sie redundant, weil
`RpcRouteInfo.method` den exakten String bereits typisiert erreichbar macht.

### `endpoints.py`

```python
# Generated by pyrpckit from browser-control.openrpc.json.
# Do not edit this file manually.
from pyrpckit.client import RpcServerInfo, RpcServerVariable


class Servers:
    BROWSER_CONTROL = RpcServerInfo(
        name="browser-control",
        url=(
            "wss://{host}/api/v1/projects/{projectId}/browser-tunnel/"
            "sessions/{sessionId}/control"
        ),
        summary="Browser control over JSON-RPC 2.0 via WebSocket",
        variables={
            "host": RpcServerVariable(default="api.example.com"),
            "projectId": RpcServerVariable(
                default="00000000-0000-0000-0000-000000000000"
            ),
            "sessionId": RpcServerVariable(
                default="00000000-0000-0000-0000-000000000000"
            ),
        },
        transport="websocket",
    )
```

Für eine angenehmere Python-Oberfläche soll `resolve()` sowohl die originalen
Variablennamen als Mapping als auch generierte `snake_case`-Keywordnamen
akzeptieren. Die generierte typsichere Convenience-Funktion kann so aussehen:

```python
def browser_control_url(
    *,
    host: str = "api.example.com",
    project_id: str,
    session_id: str,
) -> str:
    return Servers.BROWSER_CONTROL.resolve(
        {
            "host": host,
            "projectId": project_id,
            "sessionId": session_id,
        }
    )
```

Die URL-Auflösung ersetzt ausschließlich deklarierte `{variable}`-Platzhalter.
Sie nimmt kein implizites URL-Encoding vor, weil eine Variable in Host, Pfad,
Query oder sogar einem vollständigen relativen URL-Teil stehen kann. Der
Contract beziehungsweise der aufrufende Transport ist für erlaubte Werte
verantwortlich.

### `models.py`

```python
# Generated by pyrpckit from browser-control.openrpc.json.
# Do not edit this file manually.
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class RpcModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NavigateParams(RpcModel):
    url: str


class Tab(RpcModel):
    id: str
    title: str | None = None


class TabsResult(RpcModel):
    items: list[Tab]


NavigateParams.model_rebuild()
Tab.model_rebuild()
TabsResult.model_rebuild()
```

Die generierten Modelle bleiben Pydantic-Modelle. Damit werden verschachtelte
Eingaben, Ergebnisse, UUIDs, Datetimes, diskriminierte Unions und `extra`
entsprechend dem Contract zur Laufzeit validiert.

### `api/navigation.py`

```python
# Generated by pyrpckit from browser-control.openrpc.json.
# Do not edit this file manually.
from pyrpckit.client import RpcClientCore

from backend.generated.rpc.browser_control.metadata import (
    BROWSER_NAV_NAVIGATE,
    BROWSER_NAV_RELOAD,
)
from backend.generated.rpc.browser_control.models import (
    NavigateParams,
    ReloadParams,
)


class NavigationApi:
    def __init__(self, rpc: RpcClientCore) -> None:
        self._rpc = rpc

    async def navigate(self, *, url: str) -> None:
        """Navigate the active tab."""
        params = NavigateParams(url=url)
        await self._rpc.request(
            BROWSER_NAV_NAVIGATE,
            params=params.model_dump(mode="json", exclude_unset=True),
            result_type=None,
        )

    async def reload(self, *, ignore_cache: bool = False) -> None:
        params = ReloadParams(ignore_cache=ignore_cache)
        await self._rpc.request(
            BROWSER_NAV_RELOAD,
            params=params.model_dump(mode="json", exclude_unset=True),
            result_type=None,
        )
```

`RpcClientCore` ist handgeschriebene Runtime und wird nicht pro Contract
dupliziert. Er kapselt das Weiterreichen des Wire-Namens, Resultatvalidierung,
bekanntes Error-Mapping und optionale Client-Hooks. Die öffentliche API bleibt
dadurch klein, während generierte Methoden keinen untypisierten
`transport.request(...)`-Boilerplate wiederholen müssen.

Falls `RpcClientCore` im ersten Umsetzungsschritt noch nicht eingeführt wird,
darf der Emitter direkt den heutigen `RpcTransport` verwenden. Das erzeugte
Consumer-Verhalten und die Paketstruktur müssen trotzdem bereits der Spec
entsprechen.

### Verschachtelte API-Gruppen

Der konfigurierte Browser-Contract benötigt nach `api_root="browser"` nur flache
Gruppen. Generell darf ein Gruppenknoten aber eigene Operationen und
Untergruppen gleichzeitig besitzen. Ohne gekürzten Root könnte beispielsweise
`api/browser/__init__.py` so aussehen:

```python
# Generated by pyrpckit from browser-control.openrpc.json.
# Do not edit this file manually.
from pyrpckit.client import RpcClientCore

from backend.generated.rpc.browser_control.api.browser.nav import BrowserNavApi
from backend.generated.rpc.browser_control.api.browser.tab import BrowserTabApi


class BrowserApi:
    def __init__(self, rpc: RpcClientCore) -> None:
        self._rpc = rpc
        self.nav = BrowserNavApi(rpc)
        self.tab = BrowserTabApi(rpc)
```

Untergruppen werden einmal im Konstruktor aufgebaut. Ein Property, das bei
jedem Zugriff ein neues Objekt erzeugt, ist zu vermeiden. API-Gruppen sind
leichtgewichtige Views auf denselben Client-Core.

### `errors.py`

OpenRPC liefert standardmäßig nur Code und Message. Für stabile generierte
Klassennamen sollte der Renderer zusätzlich `x-rpckit-name` und optional ein
Schema für `data` emittieren:

```json
{
  "code": -32004,
  "message": "Browser tab not found",
  "x-rpckit-name": "BrowserTabNotFound",
  "x-rpckit-data-schema": {
    "$ref": "#/components/schemas/BrowserTabNotFoundData"
  }
}
```

Daraus entsteht:

```python
# Generated by pyrpckit from browser-control.openrpc.json.
# Do not edit this file manually.
from typing import ClassVar

from pyrpckit.client import RpcRemoteError

from backend.generated.rpc.browser_control.models import BrowserTabNotFoundData


class BrowserTabNotFoundError(RpcRemoteError):
    code: ClassVar[int] = -32004
    data: BrowserTabNotFoundData
```

Ohne `x-rpckit-name` bleibt der Fehler ein `RpcRemoteError`; der Generator darf
keinen Klassenbezeichner aus dem möglicherweise veränderlichen Message-Text
ableiten. Der Client-Core mappt nur Fehler, die an der konkret aufgerufenen Route
deklariert sind. Derselbe Code in zwei unabhängigen Contracts oder Kontexten
führt dadurch nicht versehentlich zum falschen Typ.

### `client.py`

```python
# Generated by pyrpckit from browser-control.openrpc.json.
# Do not edit this file manually.
from collections.abc import AsyncIterator
from typing import Self

from pydantic import TypeAdapter
from pyrpckit.client import RpcClientCore, RpcTransport

from backend.generated.rpc.browser_control.api.navigation import NavigationApi
from backend.generated.rpc.browser_control.api.session import SessionApi
from backend.generated.rpc.browser_control.api.tabs import TabsApi
from backend.generated.rpc.browser_control.metadata import HEALTH
from backend.generated.rpc.browser_control.models import BrowserUpdate


_NOTIFICATION_MESSAGE_ADAPTER = TypeAdapter(BrowserUpdate)


class BrowserControlClient:
    def __init__(
        self,
        transport: RpcTransport,
        *,
        close_transport: bool = True,
    ) -> None:
        self._rpc = RpcClientCore(
            transport,
            close_transport=close_transport,
        )
        self.navigation = NavigationApi(self._rpc)
        self.tabs = TabsApi(self._rpc)
        self.session = SessionApi(self._rpc)

    async def health(self) -> None:
        await self._rpc.request(HEALTH, result_type=None)

    async def notifications(self) -> AsyncIterator[BrowserUpdate]:
        async for message in self._rpc.notifications():
            yield _NOTIFICATION_MESSAGE_ADAPTER.validate_python(message)

    async def close(self) -> None:
        await self._rpc.close()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()
```

Der Client besitzt den übergebenen Transport standardmäßig. Das passt zur
üblichen Verwendung `async with Client(Transport(...))`. Für bewusst geteilte
Transporte verhindert `close_transport=False`, dass das Schließen eines Clients
die anderen Clients beendet:

```python
control = BrowserControlClient(shared_transport, close_transport=False)
diagnostics = BrowserDiagnosticsClient(shared_transport, close_transport=False)
try:
    ...
finally:
    await shared_transport.close()
```

`close()` und `RpcClientCore.close()` müssen idempotent sein. Ein API-Gruppenobjekt
besitzt niemals den Transport und darf ihn nicht schließen.

### `__init__.py`

Der Top-Level-Export bleibt klein:

```python
from backend.generated.rpc.browser_control.client import BrowserControlClient
from backend.generated.rpc.browser_control.endpoints import (
    Servers,
    browser_control_url,
)

__all__ = [
    "BrowserControlClient",
    "Servers",
    "browser_control_url",
]
```

Modelle werden aus `<package>.models`, Fehler aus `<package>.errors` und
Routenmetadaten aus `<package>.metadata` importiert. Sämtliche Modelle und
API-Gruppen am Package-Root zu re-exportieren erzeugt bei größeren Contracts
eine unübersichtliche und kollisionsanfällige API.

## Benennungsregeln

### Contract und Root-Client

Der explizite CLI-Wert `--client-name` hat Vorrang. Ohne Override wird
`info.title` in PascalCase umgewandelt und `Client` angehängt:

```text
Backend Browser Tunnel -> BackendBrowserTunnelClient
```

Für öffentliche SDKs ist ein expliziter kurzer Name wie `BrowserControlClient`
empfohlen. Ein ungültiger oder kollidierender expliziter Name ist ein
Generatorfehler und wird nicht still korrigiert.

### Route zu Python

Wire-Segmente und Operationsnamen werden in idiomatisches `snake_case`
umgewandelt:

```text
openBrowserTunnelSession -> open_browser_tunnel_session
browser-tab              -> browser_tab
from                     -> from_
```

Klassennamen verwenden PascalCase:

```text
browser.nav -> BrowserNavApi
```

Der Converter muss Akronyme stabil behandeln, beispielsweise
`parseURL` zu `parse_url` und `HTTPStatus` zu `http_status`.

### Kollisionen

Verschiedene Wire-Namen können auf denselben Python-Namen fallen:

```text
browser.open-tab
browser.open_tab
browser.openTab
```

Der Generator muss in diesem Fall mit allen kollidierenden Wire-Namen und dem
entstandenen Python-Namen abbrechen. Automatische Suffixe wie `_2` sind nicht
stabil und werden nicht verwendet. Ein späteres `x-rpckit-python-name` kann eine
bewusste Auflösung erlauben.

Dasselbe gilt für:

- eine Operation und Untergruppe mit gleichem Namen,

- zwei Schemas mit gleichem Python-Klassennamen,

- einen Schema- und Runtime-Import mit gleichem Namen,

- zwei Servervariablen mit identischem `snake_case`-Namen.

### Dateipfade

Nur normalisierte Python-Identifier werden zu Verzeichnis- und Dateinamen. Der
Generator schreibt niemals einen unvalidierten Contractwert als Pfad. Damit
werden sowohl ungültige Imports als auch Pfadtraversal verhindert.

## Parameter- und Ergebnismodell

### Aufrufsignaturen

Top-Level-Felder des Params-Schemas werden als keyword-only Parameter
exponiert. Verschachtelte Objekte bleiben Modelle:

```python
await client.input.click(
    target=Target(selector="#submit"),
    button=MouseButton.LEFT,
)
```

Es wird nicht zusätzlich für jede Operation eine zweite Methode wie
`click_with_params(params)` generiert. Wer ein Params-Modell dynamisch besitzt,
kann den expliziten Low-Level-Core oder eine später separat spezifizierte
`request`-Überladung verwenden. Die normale API bleibt eindeutig.

### Serialisierung

- Die Eingabe wird durch das generierte Params-Modell validiert.

- Serialisierung verwendet JSON-kompatiblen Modus.

- Nicht gesetzte Werte werden ausgelassen.

- Explizites `None` bleibt erhalten, wenn das Schema `null` erlaubt.

- Aliase aus dem JSON Schema werden beim Wire-Dump verwendet.

- Der Generator sendet `params=None`, wenn die Operation kein Params-Schema
  besitzt. Ein leeres Params-Modell wird als `{}` gesendet.

### Ergebnisse

- Modelle verwenden `Model.model_validate(result)`.

- Skalare, Container, Unions und Typaliase verwenden einen vorab auf Klassen-
  oder Modulebene erzeugten `TypeAdapter`; nicht bei jedem Aufruf einen neuen
  Adapter.

- Eine JSON-RPC-Methode ohne Ergebnis validiert die erwartete Wire-Antwort als
  `null`, gibt in Python aber `None` zurück.

- Unerwartete Resultate werden als eigener `RpcResponseValidationError`
  geworfen. Die Exception enthält den Wire-Namen und verkettet den
  Pydantic-Fehler als Ursache.

## Routenmetadaten und Client-Hooks

Die neuen Routenmetadaten sind nützlich, sollten aber die einfache
Transport-Schnittstelle nicht aufblähen. Vorgeschlagen ist folgende Trennung:

```python
class RpcTransport(Protocol):
    async def request(
        self,
        method: str,
        params: dict[str, JsonValue] | None = None,
    ) -> JsonValue: ...

    def notifications(self) -> AsyncIterator[dict[str, JsonValue]]: ...

    async def close(self) -> None: ...
```

und darüber:

```python
class RpcClientHook(Protocol):
    async def before_request(
        self,
        route: RpcRouteInfo,
        params: JsonValue,
    ) -> None: ...

    async def after_response(
        self,
        route: RpcRouteInfo,
        result: JsonValue,
    ) -> None: ...
```

Der `RpcClientCore` kennt die Route, der Transport nur den standardkonformen
Wire-Namen. So können Logging, Tracing, Metriken, Deprecation-Warnungen und
Error-Mapping Tags und weitere Metadaten nutzen, ohne HTTP- oder
WebSocket-spezifische Anforderungen in `RpcTransport` einzubauen.

Hooks sind optional. Sie dürfen für den ersten Generator-Schnitt entfallen,
solange `RpcRouteInfo` bereits erzeugt und intern durch die API-Methode geführt
wird.

## TypeScript-Client

Der TypeScript-Client folgt demselben sprachneutralen IR und derselben
Contractgrenze wie der Python-Client. Er soll sich trotzdem wie idiomatisches
TypeScript anfühlen und nicht wie transliteriertes Python.

### Delta zum heutigen TypeScript-Generator

Die bestehende TypeScript-Ausgabe besitzt bereits zwei gute Grundlagen, die
erhalten bleiben:

- Operationen nehmen ein benanntes Params-Objekt entgegen.

- Der Root-Client baut aus Punkt-separierten Methoden einen verschachtelten
  Attributbaum.

Die Zielspezifikation ändert beziehungsweise ergänzt jedoch:

- `NamespaceClient`-Klassen werden zu fachlichen `Api`-Gruppen.

- API-Gruppen wandern aus der einen großen `client.ts` in nachvollziehbare
  Dateien unter `api/`.

- Der explizite `api_root` und `api_names` vermeiden stotternde Call-Sites.

- `notifications()` heißt auf Transport- und öffentlicher Oberfläche gleich.

- Tags, Server, Routen, Deprecations und deklarierte Fehler werden nicht mehr
  beim Lowering verworfen, sondern generiert.

- `RpcMethod` als reine String-Konstante wird durch reichere
  `RpcRouteInfo`-Metadaten ergänzt.

- Die Grenze zwischen statischer Typisierung und echter Runtime-Validierung
  wird explizit gemacht.

Die semantische Zuordnung bleibt gleich:

| Konzept | Python | TypeScript |
| --- | --- | --- |
| Root-Client | `BrowserControlClient` | `BrowserControlClient` |
| API-Gruppe | `NavigationApi` | `NavigationApi` |
| Operation | `navigate(url=...)` | `navigate({ url: ... })` |
| Notification-Stream | `AsyncIterator[BrowserUpdate]` | `AsyncIterable<BrowserUpdate>` |
| optionales Feld | `UnsetType` | optionale Property `?` |
| explizites Null | `None` | `null` |
| Modell | Pydantic-Klasse | `type` oder `interface` |
| diskriminierte Union | Pydantic Discriminator | TypeScript Literal Union |
| Lifecycle | `async with` / `close()` | `try/finally` / `close()` |

### Öffentliche TypeScript-API

Mit `api_root="browser"` sowie den Gruppennamen `nav -> navigation` und
`tab -> tabs` sieht die normale Verwendung so aus:

```typescript
import { BrowserControlClient } from "@/generated/rpc/browser-control";
import { WebSocketTransport } from "@/rpc/websocket-transport";

const transport = new WebSocketTransport(
  "wss://api.example.com/api/v1/projects/p-123/" +
    "browser-tunnel/sessions/s-456/control",
);
const browser = new BrowserControlClient(transport);

try {
  await browser.navigation.navigate({ url: "https://example.com" });
  const tabs = await browser.tabs.list();
  const firstTab = tabs.items[0];
  if (firstTab !== undefined) {
    await browser.tabs.activate({ tabId: firstTab.id });
  }
} finally {
  await browser.close();
}
```

TypeScript besitzt keine keyword-only Parameter. Jede Operation mit Parametern
akzeptiert deshalb genau ein benanntes Parameterobjekt. Das ist lesbar, lässt
sich destrukturieren und ist bei neuen optionalen Feldern rückwärtskompatibel:

```typescript
await browser.navigation.navigate({ url });
await browser.input.click({ target, button: "left" });
```

Nicht empfohlen sind positional arguments:

```typescript
await browser.input.click(target, "left", undefined, false);
```

Eine Methode ohne Parameter erhält kein künstliches leeres Objekt:

```typescript
const tabs = await browser.tabs.list();
```

Sind alle Parameter optional, erhält das Objekt den Default `{}`:

```typescript
await browser.navigation.reload();
await browser.navigation.reload({ ignoreCache: true });
```

### TypeScript-Notifications

Notifications werden als diskriminierte Union erzeugt und über ein
`AsyncIterable` bereitgestellt:

```typescript
import type { BrowserUpdate } from "@/generated/rpc/browser-control/models";

function assertNever(value: never): never {
  throw new Error(`Unhandled browser notification: ${JSON.stringify(value)}`);
}

for await (const notification of browser.notifications()) {
  switch (notification.type) {
    case "urlChanged":
      console.log("new URL", notification.url);
      break;
    case "tabClosed":
      console.log("closed", notification.tabId);
      break;
    default:
      assertNever(notification);
  }
}
```

Der Literal-Discriminator ermöglicht normales TypeScript-Narrowing und eine
optionale Exhaustiveness-Prüfung über `never`. Das Notification-Modell benötigt
dafür keine generierten Klassen und kein `instanceof`.

Wie bei Python heißt die Methode sowohl im Low-Level-Transport als auch am
öffentlichen Root-Client `notifications()`.

### TypeScript-Ordnerstruktur

Für denselben Browser-Control-Contract wird folgendes Leaf-Verzeichnis erzeugt:

```text
browser-control/
├── index.ts
├── client.ts
├── endpoints.ts
├── errors.ts
├── metadata.ts
├── models.ts
├── core.ts
└── api/
    ├── index.ts
    ├── navigation.ts
    ├── tabs.ts
    └── session.ts
```

| Datei | Inhalt |
| --- | --- |
| `index.ts` | kleiner öffentlicher Barrel-Export |
| `client.ts` | Root-Client, Lifecycle, Root-Operationen und Notifications |
| `endpoints.ts` | Serverdeskriptoren und URL-Funktionen |
| `errors.ts` | contract-spezifische Remote-Fehler |
| `metadata.ts` | Contract- und Routenmetadaten |
| `models.ts` | reine TypeScript-Datentypen und Unions |
| `core.ts` | private, einmalige Verbindung zwischen APIs und Transport |
| `api/…` | API-Gruppen und Operationsmethoden |

`core.ts` ist ein internes Modul des generierten Pakets. API-Dateien dürfen
es importieren; `index.ts` exportiert es nicht.

Mehrere TypeScript-Clients werden genau wie in Python als unabhängige
Geschwister generiert:

```text
src/generated/rpc/
├── index.ts                    # handgeschrieben
├── browser-control/            # ein OpenRPC-Contract
│   ├── index.ts
│   ├── client.ts
│   ├── models.ts
│   └── api/...
├── browser-session/            # ein OpenRPC-Contract
│   ├── index.ts
│   ├── client.ts
│   ├── models.ts
│   └── api/...
└── project-admin/              # ein OpenRPC-Contract
    ├── index.ts
    ├── client.ts
    ├── models.ts
    └── api/...
```

Der Generator besitzt wieder nur das jeweilige Leaf-Verzeichnis. Der
handgeschriebene Parent-Barrel kann bewusst festlegen, welche Clients die
Anwendung global anbietet:

```typescript
export { BrowserControlClient } from "./browser-control";
export { BrowserSessionClient } from "./browser-session";
```

Standardmäßig werden keine `package.json`, `tsconfig.json` oder
Bundler-Konfigurationen erzeugt. Der Client lebt in der Toolchain des Consumers.
Ein späterer expliziter SDK-Package-Modus kann diese Dateien ergänzen, darf aber
nicht das normale Repository-Layout verkomplizieren.

### Generierte TypeScript-Dateien

#### `metadata.ts`

```typescript
// Generated by pyrpckit from browser-control.openrpc.json.
// Do not edit this file manually.
import type { RpcContractInfo, RpcRouteInfo } from "./core";

export const contract = {
  title: "Backend Browser Tunnel",
  version: "2.0.0",
  protocolVersion: 2,
} as const satisfies RpcContractInfo;

export const routes = {
  browserNavNavigate: {
    method: "browser.nav.navigate",
    summary: "Navigate the active tab.",
    tags: ["browser", "control"],
    deprecated: false,
    errorCodes: [],
  },
  browserTabActivate: {
    method: "browser.tab.activate",
    summary: "Activate a browser tab.",
    tags: ["browser", "control"],
    deprecated: false,
    errorCodes: [-32004],
  },
  browserNavReload: {
    method: "browser.nav.reload",
    summary: "Reload the active tab.",
    tags: ["browser", "control"],
    deprecated: false,
    errorCodes: [],
  },
} as const satisfies Record<string, RpcRouteInfo>;
```

`as const satisfies` erhält Literaltypen, ohne die strukturelle Prüfung gegen
die Runtime-Schnittstelle aufzugeben. Der Transport erhält weiterhin nur
`route.method`.

#### `models.ts`

```typescript
// Generated by pyrpckit from browser-control.openrpc.json.
// Do not edit this file manually.
export type NavigateParams = {
  url: string;
};

export type ReloadParams = {
  ignoreCache?: boolean;
};

export type Tab = {
  id: string;
  title?: string | null;
};

export type TabsResult = {
  items: Tab[];
};

export type BrowserUrlChanged = {
  type: "urlChanged";
  url: string;
};

export type BrowserTabClosed = {
  type: "tabClosed";
  tabId: string;
};

export type BrowserUpdate = BrowserUrlChanged | BrowserTabClosed;
```

JSON-Properties werden an der TypeScript-Oberfläche in `camelCase` angeboten,
wenn der Generator sie beim Serialisieren eindeutig auf den originalen
Wire-Namen zurückabbilden kann. Falls das aktuelle IR nur Wire-Objekte ohne
Aliasinformationen ausdrückt, bleiben Property-Namen zunächst unverändert;
stilles Umbenennen ohne Serializer-Mapping ist verboten.

#### `api/navigation.ts`

```typescript
// Generated by pyrpckit from browser-control.openrpc.json.
// Do not edit this file manually.
import type { RpcClientCore } from "../core";
import { routes } from "../metadata";
import type { NavigateParams, ReloadParams } from "../models";

export class NavigationApi {
  constructor(private readonly rpc: RpcClientCore) {}

  /** Navigate the active tab. */
  navigate(params: NavigateParams): Promise<void> {
    return this.rpc.request(routes.browserNavNavigate, params);
  }

  reload(params: ReloadParams = {}): Promise<void> {
    return this.rpc.request(routes.browserNavReload, params);
  }
}
```

Innerhalb eines generierten TypeScript-Leaf-Packages sind relative
ES-Module-Imports die portable Voreinstellung. Anders als ein absoluter
Python-Package-Import hängen TypeScript-Aliase wie `@/…` von der jeweiligen
`tsconfig`- und Bundler-Konfiguration ab. Der öffentliche Consumer darf solche
Aliase selbstverständlich verwenden, wie in den Call-Site-Beispielen gezeigt.

Type-only Imports verwenden explizit `import type`. Damit ist der Output mit
`verbatimModuleSyntax` verständlich und erzeugt keine unnötigen Runtime-Imports.
Eine Generatoroption für `.js`-Suffixe kann NodeNext-Projekte unterstützen;
extensionless Specifier bleiben die Voreinstellung für Bundler-Projekte.

#### `client.ts`

```typescript
// Generated by pyrpckit from browser-control.openrpc.json.
// Do not edit this file manually.
import type { RpcTransport } from "@/rpc/transport";

import { RpcClientCore } from "./core";
import { NavigationApi } from "./api/navigation";
import { SessionApi } from "./api/session";
import { TabsApi } from "./api/tabs";
import type { BrowserUpdate } from "./models";

export class BrowserControlClient {
  readonly navigation: NavigationApi;
  readonly session: SessionApi;
  readonly tabs: TabsApi;

  readonly #rpc: RpcClientCore;

  constructor(transport: RpcTransport, options?: { closeTransport?: boolean }) {
    this.#rpc = new RpcClientCore(transport, {
      closeTransport: options?.closeTransport ?? true,
    });
    this.navigation = new NavigationApi(this.#rpc);
    this.session = new SessionApi(this.#rpc);
    this.tabs = new TabsApi(this.#rpc);
  }

  notifications(): AsyncIterable<BrowserUpdate> {
    return this.#rpc.notifications<BrowserUpdate>();
  }

  close(): Promise<void> {
    return this.#rpc.close();
  }
}
```

Die API-Gruppen sind `readonly` und werden genau einmal konstruiert. Der private
Core erscheint weder im Barrel noch an der Call-Site. `close()` ist idempotent
und schließt den Transport standardmäßig; ein geteilter Transport verwendet
`{ closeTransport: false }`.

`Symbol.asyncDispose` wird im ersten Schnitt nicht vorausgesetzt. Es benötigt
passende Target- und Library-Einstellungen im Consumer. Das universell
verständliche `try/finally` bleibt deshalb die dokumentierte Hauptform.

#### `endpoints.ts`

```typescript
// Generated by pyrpckit from browser-control.openrpc.json.
// Do not edit this file manually.
import { defineRpcServer, resolveRpcServer } from "./core";

export const servers = {
  browserControl: defineRpcServer({
    name: "browser-control",
    url:
      "wss://{host}/api/v1/projects/{projectId}/browser-tunnel/" +
      "sessions/{sessionId}/control",
    transport: "websocket",
    variables: {
      host: { default: "api.example.com" },
      projectId: {
        default: "00000000-0000-0000-0000-000000000000",
      },
      sessionId: {
        default: "00000000-0000-0000-0000-000000000000",
      },
    },
  }),
} as const;

export function browserControlUrl(params: {
  host?: string;
  projectId: string;
  sessionId: string;
}): string {
  return resolveRpcServer(servers.browserControl, params);
}
```

Auch hier erzeugen mehrere Server mehrere Einträge und URL-Funktionen, aber nur
einen `BrowserControlClient`.

#### `errors.ts`

```typescript
// Generated by pyrpckit from browser-control.openrpc.json.
// Do not edit this file manually.
import { RpcRemoteError } from "./core";
import type { BrowserTabNotFoundData } from "./models";

export class BrowserTabNotFoundError extends RpcRemoteError {
  static readonly code = -32004;

  declare readonly data: BrowserTabNotFoundData;
}
```

Ein benannter Fehlertyp wird wie in Python nur erzeugt, wenn der Contract einen
stabilen Namen über `x-rpckit-name` liefert. Die API kann ihn normal mit
`instanceof` behandeln:

```typescript
try {
  await browser.tabs.activate({ tabId });
} catch (error) {
  if (error instanceof BrowserTabNotFoundError) {
    console.warn("tab disappeared", error.data.tabId);
  } else {
    throw error;
  }
}
```

#### `index.ts`

Der öffentliche Barrel bleibt bewusst klein:

```typescript
export { BrowserControlClient } from "./client";
export { browserControlUrl, servers } from "./endpoints";
export type { BrowserUpdate } from "./models";
```

Weitere Modelle werden direkt aus `/models`, Fehler aus `/errors` und
Routenmetadaten aus `/metadata` importiert. API-Gruppen und `core.ts` werden
nicht am Root re-exportiert.

### TypeScript-Typen und Wire-Semantik

#### Optional, `undefined` und `null`

Das erzeugte Modell bildet die JSON-Semantik genau ab:

```typescript
export type UpdateTitleParams = {
  title?: string | null;
};
```

```typescript
await browser.tabs.updateTitle({});
// {}

await browser.tabs.updateTitle({ title: null });
// { "title": null }

await browser.tabs.updateTitle({ title: "Docs" });
// { "title": "Docs" }
```

Der generierte Serializer entfernt Properties mit dem Wert `undefined`, aber
niemals `null`. Consumer-Projekte sollten `exactOptionalPropertyTypes`
aktivieren, damit `title?: T` auch statisch „Property fehlt“ und nicht
automatisch „Property enthält undefined“ bedeutet. Der Client bleibt dennoch
defensiv, wenn ein Consumer ohne diese Option kompiliert.

#### Runtime-Validierung

TypeScript-Typen existieren zur Laufzeit nicht. Ein generisches
`transport.request<T>()` ist daher allein noch keine Validierung einer
unvertrauenswürdigen Serverantwort. Der Generator muss dies ehrlich behandeln:

- Der Basismodus `validation: "types"` erzeugt eine dependency-freie statische
  API und vertraut dem Transportergebnis. Dies entspricht dem heutigen
  Generatorverhalten.

- Ein späterer Modus `validation: "runtime"` erzeugt beziehungsweise verwendet
  Decoder und wirft bei einem falschen Ergebnis einen
  `RpcResponseValidationError` mit Route und Validierungsdetails.

- Der Runtime-Modus darf nicht nur einen Type-Cast erzeugen und ihn als
  Validierung bezeichnen.

Der gewünschte Modus gehört in das Generierungsmanifest und dessen
`layout_version`. Bis der Runtime-Decoder vollständig spezifiziert und
implementiert ist, bleibt `"types"` die kompatible Voreinstellung.

#### TypeScript-Qualitätsziel

Der Output muss mindestens mit folgenden strikten Annahmen überprüfbar sein:

- `strict: true`,

- `exactOptionalPropertyTypes: true`,

- `noUncheckedIndexedAccess: true`,

- `verbatimModuleSyntax: true`,

- `tsc --noEmit`, ESLint und Prettier im Consumer-Repository.

Der Generator darf keine Abhängigkeit auf DOM-Typen voraussetzen. Ein
WebSocket-Transport kann im Browser leben, der generierte Contract-Client bleibt
wie der Python-Client transportagnostisch.

## Mehrere Transports und methodenspezifische Server

### Normalfall

Ein Client erhält einen Transport. Dieser Transport bedient alle Routen des
Contracts. Das gilt auch dann, wenn das Dokument mehrere alternative Server
aufführt.

### Methodenspezifische OpenRPC-Server

OpenRPC erlaubt `servers` auch auf Methodenebene. Ein einzelner Transport kann
diese Information nicht automatisch in mehrere Verbindungen umsetzen. Deshalb
gilt zunächst:

- Methodenspezifische Server werden in `RpcRouteInfo.server_names` erhalten.

- Der normale Client sendet weiterhin über seinen einen Transport.

- Ein Consumer kann einen handgeschriebenen Multiplex-Transport einsetzen, der
  anhand des Wire-Namens routet.

- Der Generator öffnet niemals implizit Verbindungen und wählt niemals anhand
  der URL-Schemes eigenständig eine Transportimplementierung.

Eine spätere `RpcTransportRouter`-Runtime kann explizit spezifiziert werden. Sie
ist keine Voraussetzung für die erste Version.

## Generator-Konfiguration

### Einzelner Client

Die bestehende CLI bleibt für einen Contract geeignet:

```bash
rpckit generate \
  contracts/browser-control.openrpc.json \
  --language python \
  --output src/backend/generated/rpc/browser_control \
  --package backend.generated.rpc.browser_control \
  --client-name BrowserControlClient \
  --api-root browser \
  --api-name nav=navigation \
  --api-name tab=tabs
```

Für Windows entspricht dies derselben Argumentfolge ohne Bash-Zeilenfortsetzung.

### Mehrere Clients

Für zwei oder drei Clients sind mehrere explizite Befehle akzeptabel. Bei mehr
Clients soll ein manifestbasierter Batch-Modus ergänzt werden, damit lokale
Generierung und CI exakt dieselbe Konfiguration verwenden:

```toml
# rpc-clients.toml
version = 1

[[clients]]
schema = "contracts/browser-control.openrpc.json"
language = "python"
output = "src/backend/generated/rpc/browser_control"
package = "backend.generated.rpc.browser_control"
client_name = "BrowserControlClient"
api_root = "browser"
api_names = { nav = "navigation", tab = "tabs" }

[[clients]]
schema = "contracts/browser-session.openrpc.json"
language = "python"
output = "src/backend/generated/rpc/browser_session"
package = "backend.generated.rpc.browser_session"
client_name = "BrowserSessionClient"

[[clients]]
schema = "contracts/browser-control.openrpc.json"
language = "typescript"
output = "frontend/src/generated/rpc/browser-control"
client_name = "BrowserControlClient"
transport_module = "@/rpc/transport"
api_root = "browser"
api_names = { nav = "navigation", tab = "tabs" }
```

```bash
rpckit generate --config rpc-clients.toml
rpckit generate --config rpc-clients.toml --check
```

Der Batch-Modus ist nur Orchestrierung. Jeder Eintrag wird weiterhin unabhängig
aus genau einem OpenRPC-Dokument in genau ein Leaf-Package generiert.

### Generator-Besitz und veraltete Dateien

Jedes Leaf-Package erhält eine private Manifestdatei, beispielsweise
`.pyrpckit-generated.json`, die mindestens Generatorversion, Contract-Digest und
die erzeugten relativen Pfade enthält.

Bei einer erneuten Generierung dürfen nur Dateien gelöscht werden, die im alten
Manifest als generiert markiert waren und im neuen Ergebnis nicht mehr
vorkommen. Unbekannte, handgeschriebene Dateien führen zu keiner Löschung.
`--check` meldet neue, geänderte und veraltete generierte Dateien, schreibt oder
löscht aber nichts.

Damit kann etwa `browser.tab` aus einem Contract verschwinden, ohne dass eine
alte `api/browser/tab.py` unbemerkt importierbar bleibt.

## Notwendige Erweiterungen des sprachneutralen IR

Das heutige `ClientIr` benötigt für diese Oberfläche zusätzliche Informationen.
Die Zielstruktur ist konzeptionell:

```python
@dataclass(frozen=True, slots=True)
class RouteDecl:
    rpc_name: str
    operation_name: str
    path: tuple[str, ...]
    params: tuple[ParamDecl, ...]
    params_model: str | None
    result: TypeExpr
    summary: str
    description: str
    tags: tuple[str, ...]
    errors: tuple[ErrorDecl, ...]
    server_names: tuple[str, ...]
    deprecated: bool


@dataclass(frozen=True, slots=True)
class ApiNode:
    segment: str
    path: tuple[str, ...]
    operations: tuple[RouteDecl, ...]
    children: tuple[ApiNode, ...]


@dataclass(frozen=True, slots=True)
class ClientIr:
    title: str
    version: str
    protocol_version: int | None
    servers: tuple[ServerDecl, ...]
    declarations: tuple[Declaration, ...]
    root_operations: tuple[RouteDecl, ...]
    api: tuple[ApiNode, ...]
    notifications: tuple[NotificationDecl, ...]
```

`NamespaceDecl` wird damit durch einen echten Baum ersetzt. Der Baum ist für
Python und TypeScript dieselbe semantische Struktur; nur Dateilayout und
Namenskonventionen sind sprachspezifisch.

`operation_name`, `path` und `ApiNode.segment` enthalten die originalen
Contractsegmente, keine bereits normalisierten Python- oder TypeScript-Namen.
`api_root`, `api_names`, `snake_case` und `camelCase` werden erst im jeweiligen
Emitter angewendet. Dadurch bleibt das IR tatsächlich sprachneutral und beide
Emitter können Kollisionen nach ihren eigenen Identifierregeln erkennen.

Die Erreichbarkeitsanalyse für Modelle muss Parameter, Ergebnisse, Error-Data,
Notifications und Servervariablen berücksichtigen. Nicht erreichbare Components werden
weiterhin nicht generiert.

## Determinismus und Qualität

Generierter Code muss:

- bei identischem Contract und identischen Optionen byte-identisch sein,

- mit Python 3.12 bis 3.14 funktionieren,

- `ruff check` und `ruff format --check` ohne Consumer-Sonderregeln bestehen,

- vollständig typisiert sein,

- ohne Netzwerkzugriff importierbar sein,

- beim Import keine Verbindung öffnen und keine Umgebungsvariablen lesen,

- keine Zeitstempel oder absolute lokale Pfade enthalten,

- den relativen oder explizit konfigurierten Contract-Ursprung nennen,

- keine geheimen Header, Tokens oder aufgelösten produktiven URLs in den Code
  übernehmen.

Das generierte Package hat keine eigene Dependency-Metadatei. Es lebt im
Consumer-Projekt und importiert `pydantic` sowie die passende
`pyrpckit.client`-Runtime. Ein separat veröffentlichtes SDK kann dasselbe
Package in ein normales `src/`-Layout legen; die Python Packaging Authority
empfiehlt dieses Layout, um versehentliche Imports direkt aus dem
Repository-Root zu vermeiden.

## Rückwärtskompatibilität und Migration

Die Umstellung kann in vier Schritten erfolgen:

1. Das IR erhält Route, Tags, Server, Notifications und den hierarchischen
   API-Baum.

2. Der Python-Emitter erzeugt `api/`, `metadata.py`, `endpoints.py` und
   `errors.py` und den typisierten `client.notifications()`-Stream.

3. Für eine Übergangsphase können alte Namen als nicht dokumentierte Aliase
   erzeugt werden:

   ```python
   GreetingNamespaceClient = GreetingApi
   ```

   Ein solcher Modus muss explizit per Generatoroption aktiviert werden. Neue
   Clients enthalten die Aliase nicht.

4. Consumer regenerieren pro Contract atomar und stellen ihre Imports auf die
   neue Leaf-Package-Struktur um.

Eine bestehende generierte API ist normaler Consumer-Code und ihre Umbenennung
damit API-brechend, obwohl der JSON-RPC-Wire-Contract unverändert bleibt. Die
Generatorversion beziehungsweise ein `layout_version` im Manifest muss diesen
Unterschied sichtbar machen.

## Nicht empfohlen

### Eine Client-Klasse pro Router

Router sind serverseitige Kompositionsbausteine. Derselbe Router kann mehrfach
gemountet sein, und nach der Materialisierung ist seine Python-Objektidentität
nicht Teil von OpenRPC. Eine Root-Client-Klasse pro ursprünglichem Router wäre
deshalb nicht reproduzierbar.

### Tags als Python-Pakete

Tags sind eine many-to-many Klassifikation. Eine Operation unter jedem Tag zu
duplizieren erzeugt mehrere öffentliche Call-Sites für dieselbe Wire-Methode und
unklare Klassennamen.

### Eine Client-Klasse pro OpenRPC-Server

Server beschreiben Orte und Deployment-Alternativen, keine unterschiedlichen
Contracts. Client-Code pro Server würde Modelle und Operationen ohne Nutzen
duplizieren.

### Generierte konkrete HTTP- oder WebSocket-Transporte

Der Contract kann Transporthinweise enthalten, aber Authentifizierung,
Reconnect, Timeouts, Proxying und Frameworkwahl gehören in handgeschriebene
Adapter. Der Generator soll keine Abhängigkeit auf `httpx`, `websockets` oder
ein Webframework erzwingen.

### Automatisch zusammengelegte Modelle mehrerer Contracts

Namensgleichheit ist keine semantische Identität. Contract-übergreifendes
Deduping erschwert unabhängige Versionierung und kann still falsche Typen
verwenden.

## Akzeptanzkriterien

- `browser.nav.navigate` ist im unveränderten Default als
  `client.browser.nav.navigate(...)` und mit `api_root="browser"` sowie dem
  Alias `nav -> navigation` als `client.navigation.navigate(...)` erreichbar.

- Kein erzeugter Modulpfad enthält einen Punkt oder einen unnormalisierten
  Wire-Namen.

- Nur der Root-Client besitzt `close()` und Async-Context-Manager-Methoden.

- Untergeordnete Typen heißen `Api`, nicht `Client` oder `NamespaceClient`.

- Tags, Summary, Deprecation, deklarierte Fehler und methodenspezifische Server
  bleiben pro Route als Metadaten erhalten.

- `OpenRpcContract.servers` erzeugt auflösbare Endpoint-Metadaten, aber keine
  zusätzlichen Root-Clients.

- `router.notification(...)` erscheint als typisierter
  `client.notifications()`-Stream.

- Der TypeScript-Client bietet dieselbe Route als
  `browser.navigation.navigate({ url })` und Notifications als
  `AsyncIterable<BrowserUpdate>` an.

- TypeScript-Operationen mit Parametern akzeptieren genau ein benanntes Objekt;
  parameterlose Operationen verlangen kein leeres Objekt.

- TypeScript-Gruppen sind `readonly`, werden einmal erzeugt und besitzen keinen
  eigenen Lifecycle.

- TypeScript-Modelle erhalten diskriminierte Unions und unterscheiden fehlende
  Properties, `undefined` und `null` auf dem Wire.

- Optionale nullable Parameter unterscheiden ausgelassen von explizit `None`.

- Python- und Wire-Namen sind getrennt, und Namenskollisionen brechen mit einer
  konkreten Diagnose ab.

- Zwei OpenRPC-Dokumente können in zwei Geschwister-Packages generiert werden,
  ohne gemeinsame Dateien zu überschreiben.

- Ein Generatorlauf löscht ausschließlich zuvor manifestierte generierte
  Dateien im eigenen Leaf-Package.

- `--check` erkennt neue, geänderte und veraltete Dateien ohne Schreibzugriff.

- Die erzeugten Dateien bestehen Ruff und die unterstützten Python-Versionen
  3.12 bis 3.14.

- Der TypeScript-Output besteht `tsc --noEmit` unter `strict`,
  `exactOptionalPropertyTypes`, `noUncheckedIndexedAccess` und
  `verbatimModuleSyntax` sowie die Consumer-Konfiguration für ESLint und
  Prettier.

- Ein Import des Packages führt keine I/O aus und öffnet keinen Transport.

## Referenzen

- [OpenRPC 1.4.x: Server, Method, Tags und Specification Extensions](https://spec.open-rpc.org/)
- [Python Packaging User Guide: src layout vs. flat layout](https://packaging.python.org/en/latest/discussions/src-layout-vs-flat-layout/)
- [Python-Dokumentation: strukturelle Interfaces mit `Protocol`](https://docs.python.org/3/library/typing.html#typing.Protocol)
- [Pydantic: Models](https://docs.pydantic.dev/latest/concepts/models/)
- [TypeScript Handbook: Modules](https://www.typescriptlang.org/docs/handbook/2/modules.html)
- [TypeScript Handbook: Discriminated Unions und Narrowing](https://www.typescriptlang.org/docs/handbook/2/narrowing.html#discriminated-unions)
- [TypeScript: `exactOptionalPropertyTypes`](https://www.typescriptlang.org/tsconfig/exactOptionalPropertyTypes.html)
- [TypeScript: `verbatimModuleSyntax`](https://www.typescriptlang.org/tsconfig/verbatimModuleSyntax.html)
