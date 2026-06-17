# PTT Network Status – Konzept & Planung

## Problem

Wenn ein Nutzer an einem Frontend PTT drückt und spricht, hören die anderen
verbundenen Clients die Modulation nicht. Das Funkgerät ist Halbduplex – es
kann nicht gleichzeitig senden und auf der gleichen Frequenz empfangen.

## Ist-Stand

- **TX-Audio:** Client-Mikrofon → `/audio/tx` WebSocket → `TxPipeline` →
  Funkgerät (aplay). Audio kommt als G.711 ulaw am Server an.
- **RX-Audio:** Funkgerät (arecord) → `RxPipeline` → `/audio/rx` WebSocket →
  alle Clients. Läuft unabhängig von PTT.
- **PTT-Kontrolle:** `ptt_on`/`ptt_off` Socket.IO Events. `_ptt_owner` merkt
  sich, wer aktuell sendet. `ptt_status` wird an alle Clients broadcastet.
- **User-Mapping:** `_sid_users` (sid → username) existiert bereits.

## Lösung

Das TX-Audio, das am Server ankommt, wird **zusätzlich** zum Funkgerät auch an
alle anderen Clients weitergeleitet. Der Sender selbst bekommt sein Audio nicht
zurück (kein Echo).

### Phase 1: TX-Audio an andere Clients weiterleiten

**`backend/audio/tx_pipeline.py`**

Die TxPipeline empfängt bereits ulaw-Audio von Clients in `handle_audio()`.
Dort wird das Audio derzeit nur an `aplay` weitergegeben.

**Änderung:** Zusätzlich das ulaw-Audio an alle RX-WebSockets senden, **außer**
an den sendenden Client. Dafür braucht TxPipeline eine Referenz auf die
angeschlossenen RX-Clients (aktuell nur in RxPipeline als `_clients` Set).

Möglichkeit: TxPipeline bekommt eine Liste von RX-Clients + eine Zuordnung
welcher TX-WebSocket zu welchem User gehört, damit der Sender ausgeschlossen
werden kann.

Alternativ (einfacher): RxPipeline broadcastet bereits an alle RX-Clients.
TxPipeline reicht das eingehende ulaw-Audio einfach an RxPipeline weiter, die
es dann an alle Clients außer dem Sender verschickt.

### Phase 2: Echo-Vermeidung

Der sendende Client darf sein eigenes TX-Audio nicht hören. Zwei Optionen:

**Option A (client-seitig):** Browser schaltet RX-Audio stumm während
`state.pttActive === true`. Eine Zeile in `audio.js`.

**Option B (serverseitig):** TxPipeline/RxPipeline kennt den sendenden
WebSocket und schließt ihn vom Broadcast aus. Etwas mehr Logik, aber sauberer
(da nicht jeder Client stumm geschaltet werden muss).

### Phase 3: Visuelles Feedback (optional)

`ptt_status` Event den Usernamen hinzufügen (3 Zeilen serverseitig), damit
alle Clients sehen, wer gerade sendet.

## Aufwand

| Änderung | Dateien | Zeilen ca. |
|----------|---------|------------|
| TX-Audio → andere Clients | tx_pipeline.py, app.py | ~15 |
| Echo-Vermeidung (Option A) | audio.js | ~3 |
| PTT-Status mit Username | socketio_server.py, control.js, app.js | ~15 |

**Gesamt: ~30 Zeilen.**
