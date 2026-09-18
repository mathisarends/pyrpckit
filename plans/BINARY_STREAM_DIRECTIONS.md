# Entwurf: Client→Server- und bidirektionale Binary-Streams

Status: Phase 1 und 2 umgesetzt (Server, Contract, Testclient, generierte
Clients). Phase 3, die Cara-Migration, steht noch aus.

Abweichungen bei der Umsetzung:

- `RpcBinaryInput`/`RpcBinaryOutput` sind konkrete Klassen, die pyrpckit wie
  `RpcConnection` erzeugt. Sie sind keine Protocols.
- Ein `send()` auf `RpcBinaryOutput` nach dem Close löst serverseitig
  `RpcDisconnect` aus. `RpcStreamClosed` gibt es nur in generierten Clients.
- In TypeScript schließt eine Sink-Connection nur über `await end()` ab.
  Dispose ohne `end()` gilt als Abbruch, denn `asyncDispose` kann Exceptions
  nicht erkennen.
- Die optionale Erweiterung `x-rpckit-schema` für typisierte Pfad-Variablen in
  Clients ist noch nicht umgesetzt.

Dieser Entwurf schließt die Lücke aus Abschnitt 8 von
`FEEDBACK_FOR_v0.5.0.md`: Caras Voice-Media-Socket
(`/v1/voice-sessions/{voice_session_id}/media`) soll ohne eigenes Framing, eigene
Auth-Sonderwege und einen handgeschriebenen `MediaTransport` auf pyrpckit laufen.
0.5/0.6 unterstützen nur Server→Client. Die Richtung ist heute an fünf Stellen
festgeschrieben: `protocol.py:301-314`, `runtime.py:262-272`,
`contract.py:134`, `codegen/ir.py:591` und in beiden `streams`-Templates.

## Ziele

1. Client→Server-Streams, z. B. für Uploads und Mikrofon-Audio.
2. Bidirektionale Streams auf *einer* Verbindung mit unabhängigen Richtungen:
   Input und Output laufen nebenläufig und werden nicht im Wechsel verarbeitet.
3. Output, den die Anwendung push-basiert erzeugt, etwa `VoiceTurnService`
   publiziert Frames, ohne ihn in einen Async-Generator zu zwängen.
4. Ein definiertes Ende des Inputs, ohne die Verbindung zu schließen.
   Das entspricht Caras `INPUT_END`.
5. Typisierte Pfad-Variablen im Stream-Handler statt
   `RpcConnection.path_params` als String.
6. Ein dokumentierter Weg für exklusive Ownership pro Ressource.
7. Backpressure in beide Richtungen, begrenzter Speicher und
   `max_message_bytes` auch für eingehende Frames.
8. Generierte Clients in Python und TypeScript mit derselben Semantik.

## Nicht-Ziele

- **Kein Framing in der Library.** Jede WebSocket-Binary-Message ist genau ein
  opaker Frame. Sequenznummern oder Turn-Index gehören in den Payload
  (siehe [Framing](#framing)).
- **Keine Rückgabewerte** eines Client→Server-Streams, etwa die ID eines
  Uploads. Ergebnisse laufen über eine reguläre RPC-Methode oder über
  anschließenden Output auf einem bidirektionalen Stream.
- **Kein Multiplexing** mehrerer Streams über einen Socket. Ein Stream ist
  weiterhin ein eigener WebSocket-Endpoint.
- **Keine Wiederaufnahme** nach Verbindungsabbruch.
- **Keine Rückkehr des Connect-Hooks.** Auth bleibt wie in 0.6 im Framework
  vor `serve()`.

## Überblick

Die Richtung eines Streams ergibt sich aus der Handler-Signatur. Einen
`direction=`-Parameter gibt es nicht. So kann die Deklaration nicht von der
Implementierung abweichen, und Pyright prüft die Verwendung.

| Handler-Form | Richtung |
|---|---|
| Async-Generator `-> AsyncIterator[bytes]` | `server-to-client` (heute, unverändert) |
| Coroutine mit `Inject[RpcBinaryOutput]` | `server-to-client`, push-basiert |
| Coroutine mit `Inject[RpcBinaryInput]` | `client-to-server` |
| Coroutine mit `Inject[RpcBinaryInput]` und `Inject[RpcBinaryOutput]` | `bidirectional` |

Ein Async-Generator mit `RpcBinaryInput` wird bei der Definition abgelehnt.
Die Fehlermeldung verweist auf die Coroutine-Form mit `RpcBinaryOutput`.
Grund: Input nebenläufig zu konsumieren, während ein Generator `yield`s
ausführt, erfordert eine TaskGroup über `yield` hinweg. Das bricht bei
Abbruch und `aclose()` zuverlässig.

## Server-API

### Neue Typen

```python
class RpcBinaryInput(Protocol):
    """Frames the client sends; iteration ends when the client ends its input."""

    def __aiter__(self) -> AsyncIterator[bytes]: ...
    async def receive(self) -> bytes: ...  # raises RpcInputEnded after the end
    @property
    def ended(self) -> bool: ...


class RpcBinaryOutput(Protocol):
    """Frames the server sends; waits while the socket applies backpressure."""

    async def send(self, frame: bytes | bytearray | memoryview) -> None: ...
```

Beide werden wie `RpcConnection` pro Verbindung in den Kontext gelegt und per
`Inject[...]` angefordert. Dadurch bleibt die Regel „Stream-Parameter nutzen
`Inject[T]` oder sind Pfad-Variablen“ gleich, und die Richtungserkennung liest
nur `injected_parameters`.

### Decorator

```python
@channel.stream(
    name=None,
    *,
    content_type="application/octet-stream",        # Output-Frames
    input_content_type=None,                       # Default: content_type
    summary=None,
)
```

`input_content_type=` ist nur bei Streams mit Input erlaubt. Ohne Input ist die
Angabe ein `ProtocolDefinitionError`.

### Beispiele

Client→Server:

```python
@uploads.stream("audio", input_content_type="audio/pcm")
async def upload_audio(
    frames: Inject[RpcBinaryInput],
    store: Inject[RecordingStore],
) -> None:
    async with store.open() as recording:
        async for frame in frames:
            await recording.write(frame)
```

Bidirektional mit push-basiertem Output, hier Caras Media-Socket:

```python
@voice.stream("media", content_type="audio/pcm")
async def media(
    voice_session_id: UUID,                       # Pfad-Variable, validiert
    frames: Inject[RpcBinaryInput],
    output: Inject[RpcBinaryOutput],
    connection: Inject[RpcConnection],
    owners: Inject[MediaOwners],
    turns: Inject[VoiceTurnService],
) -> None:
    async with owners.claim(voice_session_id) as claimed:
        if not claimed:
            await connection.close(
                RpcConnectionClose.POLICY_VIOLATION,
                reason="Voice session already has a media owner",
            )
            return
        async with asyncio.TaskGroup() as group:
            group.create_task(turns.consume_input(voice_session_id, frames))
            async for chunk in turns.output(voice_session_id):
                await output.send(chunk)


app.stream("/v1/voice-sessions/{voice_session_id}/media", media)
```

### Pfad-Variablen

Parameter ohne `Inject[...]` sind bei Streams Pfad-Variablen.

- **Definition:** Das Modell wird mit Pydantic gebaut, wie heute für
  Method-Params. Erlaubt sind Typen, die sich aus einem String validieren
  lassen, etwa `str`, `int`, `UUID`, `Enum` oder `Annotated[str, ...]`.
- **Mount:** `service.stream(path, ...)` prüft, dass jeder Parameter eine
  Variable des Pfads ist. Pfad-Variablen ohne Parameter bleiben erlaubt und
  sind weiter über `RpcConnection.path_params` erreichbar.
- **Verbindung:** Die Werte werden *vor* `accept()` validiert. Schlägt die
  Validierung fehl, wird der Handshake mit `RpcRejection.NOT_FOUND` abgelehnt,
  also HTTP 404 bzw. Close 1008. Die Begründung: Der Pfad bezeichnet keine
  Ressource. Dafür bekommt `_prepare()` in `runtime.py` einen optionalen
  Validierungsschritt vor `socket.accept()`.
- **Contract:** Die Server-Variable erhält zusätzlich
  `x-rpckit-schema` mit dem JSON-Schema des Parameters, etwa
  `{"type": "string", "format": "uuid"}`. Generierte Clients können damit
  `UUID` statt `str` annehmen. Diese Erweiterung ist optional und kann in
  Phase 2 folgen.

JSON-RPC-Sockets übernehmen das bewusst nicht. Dort bleiben Pfad-Variablen über
`RpcConnection` erreichbar. Eine spätere Angleichung ist möglich, gehört aber
nicht in diesen Entwurf.

### Ownership

Seit 0.6 gibt es keinen Connect-Hook mehr, und dieser Entwurf führt ihn nicht
wieder ein. Exklusive Ownership ist Anwendungslogik im Handler, wie im
Beispiel oben:

- Den Anspruch hält ein `async with` für genau die Lebensdauer des Handlers.
  Abbruch, Disconnect und Exceptions geben ihn in jedem Fall frei.
- Eine Ablehnung erfolgt *nach* `accept()` als Close
  `POLICY_VIOLATION` (1008) mit Begründung. Für Clients ist das gleichwertig zu
  einem abgelehnten Handshake. Der generierte Client meldet es als
  `RpcStreamRefused(code, reason)` (siehe [Clients](#generierte-clients)).
- Wer die Ablehnung zwingend vor dem Handshake braucht, prüft wie bei Auth in
  der eigenen FastAPI-Route vor `serve()`. Das bleibt ein bewusstes
  Framework-Thema.

`docs/streams.md` bekommt dafür ein Rezept mit einem `MediaOwners`-Registry
(Dict plus `asyncio.Lock`) als APP-scoped Dependency.

## Wire-Protokoll

Ein Stream-Socket bleibt ein eigener WebSocket-Endpoint.

| Message | Richtung | Bedeutung |
|---|---|---|
| Binary | Server→Client | Ein Output-Frame |
| Binary | Client→Server | Ein Input-Frame (nur bei `client-to-server`/`bidirectional`) |
| Text `{"type":"end"}` | Client→Server | Ende des Inputs (Half-Close) |
| Close | beide | Ende der Verbindung |

### Ende des Inputs

WebSocket kennt kein Half-Close. Deshalb gibt es genau eine reservierte
Text-Message als Steuerzeichen:

- `{"type":"end"}` beendet den Input. Die Iteration über `RpcBinaryInput`
  endet normal. Der Output läuft weiter, bis der Handler zurückkehrt.
- Eine andere Text-Message, Binary-Input nach `end` oder Input auf einem
  `server-to-client`-Stream führt zu Close `PROTOCOL_ERROR` (1002), wie heute.
- Schließt der Client die Verbindung *vor* `end`, gilt das als **Abbruch**,
  auch mit Code 1000. Der Handler wird abgebrochen, und `aclose()`/`finally`
  laufen. So bleibt ein vollständiger Upload von einem abgerissenen
  unterscheidbar.

### Abschluss durch den Server

| Handler-Ergebnis | Close |
|---|---|
| kehrt normal zurück | `NORMAL` (1000) |
| `connection.close(code, reason)` | der angegebene Code |
| Exception | `INTERNAL_ERROR` (1011), geloggt |
| Frame > `max_message_bytes` empfangen | `MESSAGE_TOO_BIG` (1009) |
| Client bricht ab | kein Close vom Server; Observer sieht den Code des Clients |

Bei `client-to-server` ist der Close-Code des Servers das Ergebnis. Nach
`end()` wartet der Client auf den Close und meldet einen Code ungleich 1000 als
Fehler.

### Framing

Die Library bleibt opak: Eine Message ist ein Frame. Für Caras bisherige
Frame-Typen (`INPUT_PCM`, `INPUT_END`, Assistant-Audio) gilt:

- `INPUT_PCM` → Input-Frame
- `INPUT_END` → `{"type":"end"}`, falls damit der gesamte Input endet
- Endet nur ein *Turn*, gehört das Signal in den Payload, etwa als festes
  Header-Byte, oder in einen separaten RPC-Aufruf
  (`voice.turn.playback_completed` existiert bereits).

Die Doku empfiehlt für Turn- oder Sequenz-Informationen einen kleinen festen
Header im Payload und verzichtet auf ein Library-Format.

## Runtime

`serve_stream_endpoint` wird in eine gemeinsame Hülle und drei Modi
aufgeteilt. Die Hülle umfasst `_prepare`, Scopes, Observer und Close.
Der heutige Generator-Modus bleibt unverändert.

**Reader-Task (bei Input):** Liest `socket.receive()` in eine
`asyncio.Queue(maxsize=limits.max_queue_size)`.

- Ist die Queue voll, wartet der Reader und liest nicht weiter. Die
  TCP-Backpressure reicht so bis zum Client durch, statt Frames zu verwerfen.
- Übergroße Frames führen zu `MESSAGE_TOO_BIG`.
- `{"type":"end"}` stellt ein End-Sentinel in die Queue. Danach liest der
  Reader weiter, um Disconnect und Protokollfehler zu erkennen.
- Ein Disconnect vor `end` setzt Abbruch und bricht den Handler ab.

**Output:** `RpcBinaryOutput.send()` ruft direkt `socket.send_bytes()` auf.
Der Aufrufer wartet also so lange, wie der Transport braucht. Pufferung oder
Drop-Strategien für Echtzeit-Audio sind Aufgabe der Anwendung. Die Doku zeigt
dafür ein Beispiel mit begrenzter Queue.

**Handler-Task:** Der Handler läuft als eigener Task. Kehrt er zurück, wird
geschlossen. Wird die Verbindung beendet, wird er abgebrochen. Das Aufräumen
folgt demselben Muster wie der Fix aus 0.6 (`6397ae4`): nach Cancellation
keine ungeschützten Awaits.

**Nicht mehr verwendbar:** `send()` nach Close oder `receive()` nach Ende
lösen `RpcStreamClosed` bzw. `RpcInputEnded` aus. Sie hängen nicht.

## Contract

`x-rpckit-binary-streams` erweitert jeden Eintrag:

```json
{
  "name": "voice.media",
  "url": "ws://localhost:8000/v1/voice-sessions/{voice_session_id}/media",
  "direction": "bidirectional",
  "contentType": "audio/pcm",
  "inputContentType": "audio/pcm",
  "frameType": "binary"
}
```

- `direction` ist `server-to-client`, `client-to-server` oder `bidirectional`.
- `contentType` beschreibt weiterhin den Output. Bei `client-to-server` fehlt
  das Feld.
- `inputContentType` ist Pflicht, sobald Input existiert.
- Ein Leser, der eine Richtung nicht kennt, muss ablehnen. Das tut
  `codegen/ir.py` heute bereits.

## Generierte Clients

### Python

| Richtung | Aufruf | Ergebnis |
|---|---|---|
| server-to-client | `client.media.preview()` | `BinaryStreamConnection` (unverändert) |
| client-to-server | `client.uploads.audio()` | `BinarySinkConnection` |
| bidirectional | `client.voice.media(voice_session_id=...)` | `BinaryDuplexConnection` |

```python
async with client.voice.media(voice_session_id=session_id) as media:
    async with asyncio.TaskGroup() as group:
        group.create_task(pump_microphone(media))   # await media.send(pcm)
        async for chunk in media:                   # Output des Servers
            speaker.play(chunk)


async def pump_microphone(media: BinaryDuplexConnection) -> None:
    async for pcm in microphone.frames():
        await media.send(pcm)
    await media.end_input()
```

- `send(frame)` wartet auf den Transport, damit Backpressure wirkt.
- `end_input()` sendet `{"type":"end"}` und darf nur einmal aufgerufen werden.
- `BinarySinkConnection.end()` ruft `end_input()` auf und wartet auf den Close
  des Servers. Ist der Code nicht 1000, löst es `RpcStreamFailed(code, reason)`
  aus.
- Verlässt man `async with` mit einer Exception, schließt der Client ohne
  `end`, und der Server behandelt das als Abbruch. Bei normalem Verlassen einer
  Sink-Connection ohne vorheriges `end()` wird `end()` automatisch aufgerufen.
- `POLICY_VIOLATION` beim oder direkt nach dem Öffnen erscheint als
  `RpcStreamRefused(code, reason)`.
- `BinaryStreamTransport` bekommt optional `send()` und `end_input()`.
  Custom Transports ohne diese Methoden unterstützen nur
  `server-to-client`; der Aufruf eines anderen Streams löst dann
  `RpcStreamsUnavailableError` aus.

### TypeScript

Die TypeScript-API hat dieselbe Form:

```ts
await using media = await client.voice.media({ voiceSessionId });
await media.send(pcm);
await media.endInput();
for await (const chunk of media) play(chunk);
```

`BinaryStreamDirection` wird zu
`"server-to-client" | "client-to-server" | "bidirectional"`.

## Testen

`RpcTestClient` auf Stream-Endpoints bekommt:

```python
async with RpcTestClient(app, f"/v1/voice-sessions/{session_id}/media",
                         resolver=resolver) as media:
    await media.send_frame(b"pcm")
    await media.end_input()
    assert await media.next_frame() == b"audio"
    assert await media.closed() == (RpcConnectionClose.NORMAL, "")
```

`send_frame()` und `end_input()` lösen auf Streams ohne Input `TypeError` aus,
genau wie `next_frame()` heute auf JSON-RPC-Endpoints.

## Umsetzung in Phasen

Jede Phase ist für sich releasefähig.

**Phase 1: Server und Contract**

- `pyrpckit/streams.py` (neu): `RpcBinaryInput`, `RpcBinaryOutput`,
  `RpcStreamDirection`, `RpcInputEnded`.
- `protocol.py`: `stream_definition()` erkennt die Richtung aus der Signatur,
  trennt Pfad- von Inject-Parametern und lehnt ungültige Kombinationen mit
  lösungsorientierten Meldungen ab. `RpcStreamDefinition` bekommt
  `direction`, `input_content_type` und `path_parameters`.
- `channel.py`: `input_content_type=`.
- `service.py`: Beim Mount prüfen, dass Pfad-Parameter zu den Pfad-Variablen
  passen.
- `runtime.py`: Pre-Accept-Validierung in `_prepare`, Modi für Output/Input/
  Duplex, Reader mit begrenzter Queue.
- `contract.py`, `schema/openrpc.py`: `direction` und `inputContentType`.
- `testing.py`: `send_frame()`, `end_input()`, `closed()`.
- Exporte in `__init__.py`, `docs/streams.md`, Changelog.

**Phase 2: Generierte Clients**

- `codegen/ir.py`: Alle drei Richtungen und `inputContentType` akzeptieren.
- Python-Templates: `streams.py.j2` (Sink/Duplex, `end_input`, neue Fehler),
  Transport-Protokoll, WebSocket-Implementierung.
- TypeScript-Templates: `streams.ts.j2` analog.
- Optional: `x-rpckit-schema` für typisierte Pfad-Variablen in beiden Clients.

**Phase 3: Cara-Migration**

- Den Media-Socket auf `@voice.stream("media")` umstellen und `MediaTransport`
  im Voice-Client durch den generierten Duplex-Client ersetzen.
- Auth bleibt in der FastAPI-Route (siehe 0.6). Wird dafür eine eigene Route
  statt `dishka_router()` benötigt, ruft sie `endpoint.serve(...)` direkt auf.

## Tests

- Richtungserkennung für alle vier Formen. Generator mit Input und
  `input_content_type` ohne Input werden abgelehnt, jeweils mit
  Meldungs-Assertions.
- Pfad-Parameter: gültige Werte sind typisiert. Ungültige werden vor
  `accept()` mit `NOT_FOUND` abgelehnt. Ein Parameter ohne Pfad-Variable ist
  ein Mount-Fehler.
- Input: Reihenfolge der Frames, `end` beendet die Iteration, Frames nach
  `end` ergeben `PROTOCOL_ERROR`, übergroße Frames ergeben `MESSAGE_TOO_BIG`.
- Abbruch: Ein Disconnect vor `end` bricht den Handler ab und gibt den
  Ownership-Kontext frei, auch bei Code 1000.
- Backpressure: Ein Handler, der nicht liest, hält den Reader nach
  `max_queue_size` Frames an. Der Speicher bleibt begrenzt.
- Duplex: Input und Output laufen nebenläufig. Output nach `end` des Inputs
  wird noch zugestellt.
- Ownership-Rezept: Eine zweite Verbindung wird mit 1008 und Begründung
  geschlossen, die erste läuft weiter.
- FastAPI-Regression: 200 Duplex-Sessions über Starlettes `TestClient` ohne
  `CancelledError`, wie in 0.6 für JSON-RPC.
- Codegen: Snapshot- und Ruff-Tests für alle Richtungen. Ein Roundtrip
  generierter Client ↔ Server für Sink und Duplex in Python und TypeScript.

## Entscheidungen

Stand 2026-09-18. Diese Punkte sind festgelegt und gelten für die Umsetzung.

1. **Steuer-Message:** Das Ende des Inputs ist die Text-Message
   `{"type":"end"}`. Ein reserviertes Binary-Präfix gibt es nicht. Text ist
   lesbar und kann nie mit einem Payload kollidieren.
2. **Abbruch durch den Client:** Ein Close vor `{"type":"end"}` ist immer ein
   Abbruch, auch mit Code 1000. Einen eigenen Abbruch-Code wie 4000 gibt es
   nicht. Ein vollständiger Input endet ausschließlich über `end`.
3. **Ungültige Pfad-Variablen:** Der Handshake wird vor `accept()` mit
   `RpcRejection.NOT_FOUND` abgelehnt, nicht mit `PROTOCOL_ERROR`. Der Pfad
   bezeichnet dann keine Ressource.
4. **Pfad-Parameter für JSON-RPC-Sockets:** Nicht Teil dieses Entwurfs. Eine
   Angleichung wird separat entschieden.
5. **Namen:** `RpcBinaryInput` und `RpcBinaryOutput`. Sie benennen die
   Richtung aus Sicht des Handlers und passen zu `input_content_type=` und
   `end_input()`. `RpcByteSource`/`RpcByteSink` werden nicht verwendet.
