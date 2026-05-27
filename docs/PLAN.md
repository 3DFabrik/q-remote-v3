# Q-Remote V3 – Masterplan

_Der dritte Anlauf. Und der, der sitzt._

---

## Was wir gelernt haben

| | V1 (Wochen) | V2 (Stunden) | V3 (Plan) |
|---|---|---|---|
| Audio-Transport | SocketIO (alles) | WebRTC (geplant, nie gebaut) | **2× Raw WebSocket** |
| Audio-Codec | μ-law | Opus (nie hinbekommen) | **μ-law** (bewährt) |
| Audio-Capture | ScriptProcessorNode | AudioWorklet (geplant) | **AudioWorkletNode** |
| Backend | Flask + threading | FastAPI (geplant) | **FastAPI + async** |
| Control | SocketIO | SocketIO | **SocketIO** |
| Architektur | Monolith | Schichten (geplant) | **3 saubere Schichten** |
| Protokoll | Reverse-Engineered im Code | Doku vorhanden | **Protocol-First** |

### Kern-Erkenntnis
V2 war auf dem Papier gut, aber der Sprung war zu groß (WebRTC + Opus + FastAPI alles gleichzeitig). V3 macht das Fundament zuerst und baut darauf auf.

### Audio-Codec-Entscheidung
**μ-law bleibt.** Nicht weil es das Beste ist, sondern weil es funktioniert. Opus haben wir nie stabil bekommen. Das Problem war nie der Codec, sondern der Transport.

---

## Architektur

### Drei Schichten

```
┌─────────────────────────────────────────────────┐
│                   Frontend                       │
│         Vanilla JS + ES Modules                  │
│   Kein Build-Step, kein Framework                │
│                                                  │
│   Control ── SocketIO ──┐                        │
│   Audio   ── WS /audio ─┤                        │
│   Display ── Canvas     │                        │
│   UI      ── CSS Grid   │                        │
└──────────────────────────┼───────────────────────┘
                           │
┌──────────────────────────┼───────────────────────┐
│                   Backend │                       │
│         FastAPI + Uvicorn │                       │
│                          │                       │
│   ┌─────────────┐  ┌─────┴──────┐               │
│   │  Control    │  │  Audio     │               │
│   │  SocketIO   │  │  WebSocket │               │
│   │             │  │            │               │
│   │  - Auth     │  │  - RX WS   │               │
│   │  - Buttons  │  │  - TX WS   │               │
│   │  - Display  │  │  - μ-law   │               │
│   │  - Status   │  │  - PTT     │               │
│   │  - S-Meter  │  │            │               │
│   └──────┬──────┘  └─────┬──────┘               │
│          │               │                       │
│   ┌──────┴───────────────┴──────┐               │
│   │        Radio Layer          │               │
│   │   (Serial /dev/ttyACM0)     │               │
│   │                             │               │
│   │   - Protocol Handler        │               │
│   │   - LCD Parser              │               │
│   │   - PTT Control             │               │
│   │   - RSSI Polling            │               │
│   │   - Audio I/O (ALSA)        │               │
│   └─────────────────────────────┘               │
└─────────────────────────────────────────────────┘
```

### Transport – Zwei Kanäle

| Kanal | Protokoll | Zweck | Warum |
|-------|-----------|-------|-------|
| Control | SocketIO | Buttons, Knob, Display, Status, Auth | Bewährt, Reconnect, Events |
| Audio | Raw WebSocket | RX-Stream + TX-Stream | Kein Overhead, kein Heartbeat-Konflikt |

**Warum Raw WS für Audio?**
- SocketIO hat eigenen Heartbeat + Event-Framing → Overhead bei 47+ Events/s
- Raw WS: einfach Bytes durchschieben, kein Framing, kein Heartbeat
- Control stirbt nie an Audio-Flut – komplett getrennt
- Kein WebRTC-Setup (STUN/TURN/NAT/aiortc)

---

## Audio-Pipeline (μ-law, aber sauber)

### TX (Browser → Radio)
```
Mikrofon
  → AudioWorkletNode (eigener Thread!)
    → 8kHz Resample
    → μ-law Encode
    → 80ms Chunks bündeln (~12 Sends/s statt 47)
  → Raw WebSocket /audio/tx
  → Backend
    → μ-law decode
    → ALSA Playback (aplay/pcm)
    → Radio TX Audio In
```

### RX (Radio → Browser)
```
Radio RX Audio Out
  → ALSA Capture (arecord/pcm)
  → μ-law Encode
  → 80ms Chunks
  → Raw WebSocket /audio/rx
  → Browser
    → AudioWorkletNode
    → μ-law Decode
    → Speaker Playback
```

### Chunk-Strategie
- **Chunk-Größe:** 80ms (640 Bytes bei 8kHz μ-law)
- **Send-Rate:** ~12 Sends/s statt 47/s in V1
- **Adaptive Pufferung:** Bei schlechter Verbindung Chunk auf 120-160ms erhöhen (weniger Sends/s, mehr Latenz, aber stabiler)

---

## Projektstruktur

```
q-remote-v3/
├── config.yaml                 # Zentrale Config
├── requirements.txt
├── README.md
│
├── backend/
│   ├── __init__.py
│   ├── app.py                  # FastAPI Entry Point
│   ├── config.py               # Config-Loader
│   │
│   ├── radio/                  # Schicht 1: Radio-Layer
│   │   ├── __init__.py
│   │   ├── connection.py       # Serial Connection Management
│   │   ├── protocol.py         # Protocol Handler (CMDs, Responses)
│   │   ├── display.py          # LCD Parser
│   │   ├── commands.py         # High-Level Radio Commands
│   │   └── audio.py            # ALSA Audio I/O
│   │
│   ├── control/                # Schicht 2a: Control
│   │   ├── __init__.py
│   │   ├── socketio_server.py  # SocketIO Events
│   │   ├── auth.py             # JWT Auth + User Management
│   │   └── sessions.py         # Session / PTT-Lock
│   │
│   ├── audio/                  # Schicht 2b: Audio Bridge
│   │   ├── __init__.py
│   │   ├── ws_handler.py       # Raw WebSocket Endpoints
│   │   ├── rx_pipeline.py      # Radio → Browser
│   │   └── tx_pipeline.py      # Browser → Radio
│   │
│   └── utils/
│       ├── __init__.py
│       └── logging.py          # Strukturiertes Logging
│
├── frontend/
│   ├── index.html
│   ├── static/
│   │   ├── css/
│   │   │   └── style.css
│   │   └── js/
│   │       ├── app.js          # Main Entry, Module-Orchestrator
│   │       ├── control.js      # SocketIO Client (Buttons, Display, Status)
│   │       ├── audio.js        # Audio WebSocket + Worklet Manager
│   │       ├── display.js      # Canvas LCD Renderer
│   │       ├── smeter.js       # S-Meter mit Decay
│   │       ├── knob.js         # Drehregler
│   │       ├── auth.js         # Login/Session
│   │       └── worklet.js      # AudioWorklet Processor (eigener Thread)
│   │
│   └── templates/              # Falls nötig (Admin etc.)
│
├── tests/
│   ├── test_protocol.py        # Radio Protocol Tests (Mock Serial)
│   ├── test_audio.py           # Audio Pipeline Tests
│   └── test_auth.py            # Auth Tests
│
└── docs/
    ├── protocol.md             # Quansheng Serial Protocol Spec
    └── api.md                  # WebSocket API Spec
```

---

## Config-System (config.yaml)

```yaml
radio:
  device: "/dev/ttyACM0"
  baudrate: 115200
  timeout: 1.0

audio:
  sample_rate: 8000
  channels: 1
  chunk_ms: 80            # ms pro Chunk
  device_rx: "hw:CARD=Quansheng,DEV=0"
  device_tx: "default"

server:
  host: "0.0.0.0"
  port: 8080
  cors_origins: ["*"]

auth:
  jwt_secret: "change-me-in-production"
  session_timeout_minutes: 120
  users_file: "users.yaml"

logging:
  level: "INFO"
  format: "json"           # "json" oder "text"
```

---

## Phasenplan

### Phase 1 – Fundament (1-2 Tage)
**Ziel:** Radio Layer + Config + Basis-Backend laufen

- [ ] Projektstruktur anlegen
- [ ] Config-System (YAML + Loader)
- [ ] Radio Layer: Serial Connection mit Auto-Reconnect
- [ ] Radio Layer: Protocol Handler (Commands aus protocol.md)
- [ ] Radio Layer: Display Parser (LCD-Bytes → Canvas-Daten)
- [ ] FastAPI Basis-App mit Health-Check
- [ ] Erster Test: Display-Daten über REST abrufbar

**Deliverable:** `GET /api/status` liefert Radio-Status + Display-Daten

### Phase 2 – Control (1 Tag)
**Ziel:** Buttons + Display im Browser, SocketIO läuft

- [ ] SocketIO Server (Buttons, Display-Updates, Status)
- [ ] Frontend: Canvas LCD Renderer (aus V1 übernehmen + verbessern)
- [ ] Frontend: Button-Grid (UP, DOWN, MENU, EXIT, F, 0-9, *, #)
- [ ] Frontend: Knob (Drehregler)
- [ ] Frontend: SocketIO Client Modul
- [ ] Auth: JWT Login + einfache User-Verwaltung

**Deliverable:** Radio über Browser steuern, Display live

### Phase 3 – Audio (1-2 Tage)
**Ziel:** RX + TX Audio, sauber getrennt vom Control

- [ ] AudioWorklet Processor (`worklet.js`)
- [ ] Raw WebSocket `/audio/rx` (Radio → Browser)
- [ ] Raw WebSocket `/audio/tx` (Browser → Radio)
- [ ] Backend: RX Pipeline (ALSA → μ-law → WS)
- [ ] Backend: TX Pipeline (WS → μ-law → ALSA)
- [ ] Frontend: Audio Modul (WS + Worklet + Lautstärke)
- [ ] PTT-Taste (Pointerdown/Pointerup → TX WS open/close)
- [ ] Adaptive Chunk-Größe (bei schlechter Verbindung)

**Deliverable:** Voll funktionsfähig – hören + sprechen über Browser

### Phase 4 – Polish (1-2 Tage)
**Ziel:** Produktionsreif

- [ ] S-Meter (RSSI → dBm → S-Unit, Decay)
- [ ] PTT-Lock (nur 1 User gleichzeitig)
- [ ] Admin-Page (User anlegen/bearbeiten/löschen)
- [ ] Activity-Log (wer hat wann was gemacht)
- [ ] Battery-Voltage Anzeige
- [ ] Automatischer Reconnect (Frontend)
- [ ] Mobile-optimiertes CSS
- [ ] Systemd-Service File
- [ ] README mit Setup-Anleitung

**Deliverable:** Produktionsready

### Phase 5 – Nice-to-Have (später)
- [ ] PWA (Homescreen-Install)
- [ ] Spektrum-Analyzer
- [ ] Kanalliste / Frequenzverwaltung
- [ ] Multi-Radio Support

---

## Technologie-Stack

| Komponente | Wahl | Warum |
|-----------|------|-------|
| Backend | **FastAPI + Uvicorn** | Async, schnell, moderne Python-API |
| Control | **python-socketio** (async) | Bewährt, Reconnect, Namespaces |
| Audio Transport | **Raw WebSocket** (FastAPI WS) | Minimaler Overhead, kein Heartbeat-Konflikt |
| Audio Codec | **μ-law (G.711)** | Bewährt, keine Abhängigkeiten |
| Audio Capture | **AudioWorkletNode** | Eigener Thread, kein Main-Thread-Block |
| Auth | **JWT** | Stateless, einfach |
| Config | **YAML** | Menschenlesbar, einfach |
| Frontend | **Vanilla JS + ES Modules** | Kein Build, keine Abhängigkeiten |
| Serial | **pyserial** | Standard |
| Audio I/O | **ALSA (subprocess oder pyalsaaudio)** | Pi-Standard |
| Logging | **structlog** oder **logging + JSON** | Strukturiert, durchsuchbar |
| Reverse Proxy | **Caddy** | HTTPS automatisch, wie gehabt |

---

## Was wir aus V1 übernehmen

- Canvas LCD Renderer (konzeptionell, sauberer rewrite)
- S-Meter Decay-Logik
- Knob-Interaktion
- Login/Admin-Konzept
- Quansheng Protocol Doku (als Grundlage für protocol.py)

## Was wir NICHT übernehmen

- Flask + threading
- ScriptProcessorNode
- Audio über SocketIO
- Monolith-App.js
- Hardcoded Config
- patch_*.py Hotfix-Dateien

---

## Entscheidungen ✅

1. **ALSA → subprocess** – `arecord`/`aplay` als subprocess. Simpler, weniger Abhängigkeiten, leichter zu debuggen. Kein `pyalsaaudio` Compiling auf dem Pi.

2. **Zwei Audio-WebSockets** – `/audio/rx` (Radio→Browser, permanent) und `/audio/tx` (Browser→Radio, nur bei PTT offen). Sauber getrennt.

3. **PTT über Control-SocketIO** – PTT-Button → SocketIO Event → Backend aktiviert TX-Pfad → Browser öffnet TX-WS → Audio fließt. PTT loslassen → SocketIO Event → TX-WS wird geschlossen. Steuerung über zuverlässigen Kanal, Audio separat.

4. **YAML für Users** – Simpel, eine Handvoll User. Datei `users.yaml`.

5. **UV-K5 zuerst, Hamlib später** – Radio-Layer hinter einem Interface (`RadioAdapter`). V3 implementiert `QuanshengAdapter` (Serial). Später kommt `HamlibAdapter` dazu für andere Geräte. Architektur lässt Platz, aber nicht over-engineeren.
