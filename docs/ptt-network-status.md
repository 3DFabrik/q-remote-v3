# PTT Network Status – Konzept & Planung

## Problem

Wenn ein Nutzer an einem Frontend PTT drückt, ist für andere verbundene Clients
nicht sichtbar/hörbar, dass gerade jemand sendet.

## Ist-Stand

Die RX-Pipeline (`backend/audio/rx_pipeline.py`) nimmt das Audio vom Funkgerät
auf (ALSA → G.711 ulaw) und broadcastet es an **alle** verbundenen WebSocket-
Clients (`/audio/rx`). Das passiert bereits unabhängig davon, wer PTT drückt.

**Das heißt:** Wenn Client A PTT drückt und ins Mikrofon spricht, läuft das
Audio durch die TxPipeline zum Funkgerät. Das Funkgerät empfängt das Signal
auf der RX-Seite und die RxPipeline sendet es an alle Clients — **inklusive
Client A selbst**.

Die anderen Clients hören die Modulation also **bereits**. Was fehlt ist:

1. **Visuelles Feedback:** Kein Client weiß, *wer* gerade PTT drückt
2. **Echo-Vermeidung:** Client A hört sich selbst auf der RX-Strecke

## Ziel

- Alle Clients sehen, welcher Nutzer gerade sendet (Name, nicht nur SID)
- Der sendende Client hört sich nicht selbst (kein Echo)
- Minimaler Eingriff in den bestehenden Code

## Konzept

### 1. Server: PTT-Status mit Usernamen anpassen

**Datei:** `backend/control/socketio_server.py`

Die `ptt_status` Events werden bereits an alle Clients gesendet. Sie enthalten
aber nur die Socket.IO `sid`, nicht den Usernamen. Die Mapping-Tabelle
`_sid_users` (sid → username) existiert bereits.

**Änderung:** In `ptt_on()` und `_drain_and_release()` den Usernamen mitgeben:

```python
# ptt_on(), Zeile ~231:
# ALT:
await sio.emit('ptt_status', {'active': True, 'holder': sid})
# NEU:
await sio.emit('ptt_status', {'active': True, 'holder': sid, 'user': user})

# _drain_and_release(), Zeile ~260:
# ALT:
await sio.emit('ptt_status', {'active': False, 'holder': None})
# NEU:
await sio.emit('ptt_status', {'active': False, 'holder': None, 'user': None})
```

Auch im `disconnect`-Event (Zeile ~185) entsprechend anpassen.

Das war's serverseitig. Keine neuen Events, keine neuen Klassen.

### 2. Client: Usernamen im PTT-Status anzeigen

**Datei:** `frontend/static/js/control.js`

Der `ptt_status` Event Handler existiert bereits. Er reicht `data.active`,
`data.holder`, `data.error` weiter. Wir erweitern ihn um `data.user`:

```javascript
// ALT:
this.socket.on('ptt_status', (data) => {
    if (this.onPttStatus) this.onPttStatus(data.active, data.holder, data.error);
});

// NEU:
this.socket.on('ptt_status', (data) => {
    if (this.onPttStatus) this.onPttStatus(data.active, data.holder, data.error, data.user);
});
```

**Datei:** `frontend/static/js/app.js`

Den Callback erweitern und ein kleines Status-Element aktualisieren:

```javascript
// ALT:
control.onPttStatus = (active, holder, error) => {

// NEU:
control.onPttStatus = (active, holder, error, user) => {
```

Innerhalb des Callbacks, wenn `active && user` und `user !== eigenUser`:
→ Status-Anzeige "📡 {user} sendet..."

Wenn `!active`:
→ Status-Anzeige leeren

**Datei:** `frontend/index.html`

Ein kleines Element unter dem PTT-Button oder im Header:

```html
<div id="ptt-net-status" class="ptt-net-status"></div>
```

**Datei:** `frontend/static/css/style.css`

```css
.ptt-net-status {
    color: #e10600;
    font-size: 0.75rem;
    text-align: center;
    min-height: 1em;
}
```

### 3. Echo-Vermeidung (optional, Phase 2)

Aktuell bekommt der sendende Client sein eigenes Audio über die RX-Strecke
zurück. Das kann störend sein.

**Option A (einfach):** Client-seitig — während `state.pttActive === true`
den RX-Audio-Stream im Browser stumm schalten (Gain = 0).

**Option B (serverseitig):** RxPipeline kennt die WebSocket-Verbindungen.
Wenn ein Client PTT hält, könnte RxPipeline dessen WebSocket temporär von
der Broadcast-Liste ausschließen. Dafür müsste RxPipeline wissen, welcher
WebSocket zu welchem User gehört. Der User ist über die Session verfügbar,
aber RxPipeline arbeitet aktuell nur mit WebSocket-Objekten.

→ Option A ist deutlich einfacher und erfordert nur eine Zeile in `audio.js`.

## Aufwand

| Änderung | Dateien | Zeilen |
|----------|---------|--------|
| Server: `user` in ptt_status | socketio_server.py | ~3 Zeilen |
| Client: Callback erweitern | control.js | ~1 Zeile |
| Client: UI aktualisieren | app.js | ~10 Zeilen |
| UI: Element + CSS | index.html, style.css | ~10 Zeilen |
| Echo-Vermeidung (optional) | audio.js | ~2 Zeilen |

**Gesamt: ~25 Zeilen Änderung.**

## Commits

1. `feat: show PTT username in network status` — Server + Client Anpassungen
2. `feat: suppress RX audio echo during PTT` (optional, Phase 2)
