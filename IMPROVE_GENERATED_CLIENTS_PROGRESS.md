# Stand der Umsetzung (Fortsetzungspunkt)

Bezug: [`IMPROVE_GENERATED_CLIENTS.md`](IMPROVE_GENERATED_CLIENTS.md) — dort stehen
Befunde und Zielbild. Diese Datei hält fest, was davon **umgesetzt** ist, was
**offen** ist und **wie committet** werden soll.

Stand: Die Generator- und Runtime-Änderungen sind inkrementell committet.
`README.md` und die grundlegenden Guides wurden parallel im separaten Commit
`7dbfb36` überarbeitet und anschließend um die Client-Details ergänzt.

Alle Tests grün: `uv run pytest -q` → 180 passed, 1 skipped.
Lint/Format: `uv run ruff check pyrpckit tests examples`,
`uv run ruff format --check pyrpckit tests examples`,
`npx prettier --check examples/generated_clients/typescript`.

Regenerieren der Beispiel-Clients (neu hinzugekommen):

```bash
uv run python -m pyrpckit.codegen.cli generate --config examples/generated_clients/rpcgen.toml
```

---

## Erledigt

### 1. `errors`-Modul wird immer erzeugt (P0)

`transport.py`/`transport.ts` importierten `error_from_response` bedingungslos,
das Modul wurde aber nur bei benannten Fehlern erzeugt → generierter Client war
nicht importierbar/kompilierbar. Jetzt immer emittiert, leere Fehlerliste wird
im Template abgefangen.

- `pyrpckit/codegen/python.py`, `pyrpckit/codegen/typescript.py`
- `templates/python/errors.py.j2`, `templates/typescript/errors.ts.j2`
- Regressionstest: `tests/codegen/test_layout.py` prüft für beide Sprachen,
  dass **jeder** generierte Modulimport auf eine erzeugte Datei zeigt
  (verifiziert: schlägt ohne den Fix fehl).

### 2. Exportflächen

- Python `__init__.py`: exportiert zusätzlich Modelle, Namespace-Klassen,
  generierte Fehlerklassen, `models`-Modul.
- Python: **relative Imports** in `__init__.py` (`from .client import ...`) über
  `_Imports(relative_to=...)`.
- TS `index.ts`: `namespaces`, `errors`, `routes`/`notifications`, Fehlerklassen
  (`RpcRemoteError`, `RpcConnectionClosed`), Typen (`RpcClientCore`,
  `RpcClientHook`, `RpcRouteInfo`, `RpcTransport`, `JsonValue`), Stream-Typen.
- Neue Kollisionsprüfung `assert_unique_names("package exports", ...)`.

### 3. Optionale Parameter nutzen `UNSET` (Python)

Parameter mit Schema-Default werden nicht mehr hart im Client gesetzt
(`quality: int = 80` → `quality: int | UnsetType = UNSET`), der Server-Default
greift wieder. Test:
`test_params_with_a_schema_default_stay_unset_until_the_caller_sets_them`.

### 4. Runtime-Fixes

- **Notification-Pump-Endzustand** (py + ts): `_NotificationHub` /
  `NotificationHub` merkt sich `terminal`/`ended`; ein Subscriber nach dem Ende
  bekommt sofort Ende bzw. Fehler statt ewig zu hängen. Tests in
  `tests/codegen/test_python_runtime.py`.
- **Hooks in TS**: `RpcClientHook` (`beforeRequest`/`afterResponse`) im Core,
  durchgereicht bis `connect({ hooks })`. Python: `hooks` jetzt am Client-Ctor
  und an `connect()`.
- **Reguläres Streamende**: neuer Fehler `RpcStreamClosed` (py in
  `internal/errors.py`, ts in `streams.ts`); `async for` über einen Binary-Stream
  endet normal statt per Exception. `RpcStreamsUnavailableError` erbt in Python
  jetzt von `RpcClientError`.
- **`BinaryStreamOpening`**: `__await__` entfernt, stattdessen explizit
  `await ....open()` oder `async with`.
- Runtime nutzt PEP-695-Generics (`async def request[ResultT]`), kein `TypeVar`.

### 5. Streams hängen am Namespace

- Fixture `automation.openrpc.json`: Stream heißt jetzt
  `browser.screencast.frames` → `client.browser.screencast.frames()` in beiden
  Sprachen (vorher `client.screencast()` an der Wurzel).
- Generator: Streams nehmen an den Namens-Kollisionsprüfungen teil
  (`_validate_nodes` + Root-Member). Test:
  `test_a_stream_that_shadows_a_namespace_is_rejected`.

### 6. `connect()`-Redesign

- **Lazy**: `RpcTransportPool` (py: `internal/connection.py`, ts: `core.ts`)
  öffnet pro Server erst beim ersten Request; `eager=True`/`eager: true` öffnet
  alle parallel.
- **Ein Options-Satz**: `connect(host=..., servers={...}, request_timeout=...,
  socket_factory=..., stream_socket_factory=..., hooks=..., eager=...)`;
  TS analog als `ConnectOptions` (plus `url` bei genau einem Server).
- **Geteilte Variablen**: `host` gilt für alle Server **und** Binary-Streams;
  `resolve_endpoints(variables, overrides)` / `resolveEndpoints(variables,
  overrides)`; Streams erben die Connect-Werte über `RpcClientCore.variables`
  und `resolve_stream_endpoint(..., defaults=...)`.
- **Eine URL-Auflösung**: `resolve_url_template()` in `internal/metadata.py`,
  genutzt von Servern und Streams (Enum-Prüfung, unbekannte Variablen).
- **Konstruktionswege reduziert**: `from_transport`/`from_transports`/
  `from_transport_map` bzw. `fromTransport`/`fromTransports` → ein
  `with_transports()` / `withTransports()`.
- TS-Client hat jetzt `[Symbol.asyncDispose]` (`await using client = ...`).
- Neue Prüfung `assert_unique_names("connect options", ...)`.
- Verhaltenstests: `tests/codegen/test_connect.py` (lazy, eager, Server-Override,
  Stream erbt Host, unbekannte Stream-Variable) — alle grün.

### 7. TypeScript wird strikt kompiliert

- Neuer Smoke-Test generiert einen vollständigen TypeScript-Client und führt
  `tsc --noEmit --strict` darüber aus.
- Dabei gefundene Typfehler behoben: `subprotocols` bleibt auch bei generischen
  Transport-Deskriptoren typisiert; die `binaryStreams`-Tabelle wird gegen ihre
  TypeScript-Schlüssel statt gegen RPC-Namen geprüft.
- Der eingecheckte Beispiel-Client wurde neu generiert.

### 8. TypeScript-`connect` hat echte Verhaltenstests

- Ein Node-Harness wird zusammen mit einem frisch generierten Client strikt
  kompiliert und ausgeführt.
- Abgedeckt sind Lazy- und Eager-Verbindungen, ein einzelner Server-Override
  sowie Connect-Variablen, die an Binary-Streams vererbt werden.

### 9. Client-Dokumentation und Changelog

- `docs/clients.md` dokumentiert Lebenszyklus, Lazy-/Eager-Verbindungen,
  Multi-Server-Overrides, Endpoint-Factories, Custom-Transports und Hooks.
- `docs/streams.md` zeigt namespaced Streams, beide Python-Lebenszyklusformen,
  `await using` in TypeScript, Variablenvererbung und reguläres Streamende.
- `docs/errors.md`, `examples/README.md` und `CHANGELOG.md` wurden an die
  generierte 0.6-API angepasst.

### 10. Endpoint-Factories und Overrides

- `endpoints.<server>()` bleibt als typisierter Escape Hatch für
  serverspezifische Variablen und vertragliche Subprotokolle erhalten.
- Python und TypeScript lehnen ein Endpoint-Objekt sofort ab, wenn dessen
  `server` nicht zum Schlüssel im `servers`-Override passt.
- Verhaltenstests decken den Mismatch in beiden Sprachen ab.

---

## Offen

1. **`session()`-Convenience für Streams** (Punkt 9 der Zielliste): ein Aufruf,
   der `start()` und den Binary-Socket zusammen macht. Braucht eine
   Contract-Erweiterung, die den Stream mit seiner Startmethode verknüpft
   (z. B. `x-rpckit-binary-streams[].startMethod`), plus Serverseite
   (`channel.stream(...)`). Bewusst zurückgestellt.
---

## Commits

1. `66f8727 Always generate the client errors module`
2. `bd6f21a Export models, namespaces and errors from generated clients`
3. `0fe7de8 Leave optional params unset so server defaults apply`
4. `0e71e4b Fix notification pump and binary stream shutdown`
5. `bc1c71d Place binary streams on their namespace`
6. `2579804 Redesign connect around shared options and lazy sockets`
7. `b31362f Add generated client fixtures and connect tests`
8. `e3c84ae Type-check generated TypeScript clients`
9. `0914767 Test TypeScript client connection behavior`
10. `48c82f8 Document generated client lifecycle and streams`
11. `daea196 Document generated client breaking changes`
12. `420029f Reject mismatched endpoint overrides`
