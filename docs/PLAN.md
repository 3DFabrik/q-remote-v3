# Q-Remote V3 – Masterplan

_Der dritte Anlauf. Und der, der sitzt._

---

## Was wir gelernt haben

| | V1 (Wochen) | V2 (Stunden) | V3 (Plan) |
|---|---|---|---|
| Audio-Transport | SocketIO (alles) | WebRTC (geplant, nie gebaut) | **2× Raw WebSocket** |
| Audio-Codec | μ-law | Opus (nie hinbekommen) | **μ-law** (bewährt) |
| Audio-Capture | ScriptProcessorNode | AudioWorklet (geplant) | **ScriptProcessorNode** (läuft, Worklet-Fallback) |
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
│   │   - EEPROM Read/Write       │               │
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
  → AudioContext (ScriptProcessorNode)
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
    → AudioContext (ScriptProcessorNode)
    → μ-law Decode
    → Speaker Playback
```

### Chunk-Strategie
- **Chunk-Größe:** 80ms (640 Bytes bei 8kHz μ-law)
- **Send-Rate:** ~12 Sends/s statt 47/s in V1
- **Adaptive Pufferung:** Bei schlechter Verbindung Chunk auf 120-160ms erhöhen (weniger Sends/s, mehr Latenz, aber stabiler)

---

## Projektstruktur (IST-Zustand)

```
q-remote-v3/
├── config.yaml                 # Zentrale Config
├── requirements.txt
├── README.md
├── users.json                  # User-Datenbank (Session-basiert)
│
├── backend/
│   ├── __init__.py
│   ├── main.py                 # ASGI Entry Point
│   ├── app.py                  # FastAPI App + Routes
│   ├── auth.py                 # Session-Auth + User Management
│   ├── config.py               # Config-Loader
│   │
│   ├── radio/                  # Schicht 1: Radio-Layer
│   │   ├── __init__.py
│   │   ├── adapter.py          # Radio Adapter Interface
│   │   ├── connection.py       # Serial Connection Management
│   │   ├── protocol.py         # Protocol Handler
│   │   ├── display.py          # Display-Daten Handling
│   │   └── lcd.py              # LCD Parser
│   │
│   ├── control/                # Schicht 2a: Control
│   │   ├── __init__.py
│   │   └── socketio_server.py  # SocketIO Events (Buttons, Display, Status, Spectrum)
│   │
│   ├── audio/                  # Schicht 2b: Audio Bridge
│   │   ├── __init__.py
│   │   ├── rx_pipeline.py      # Radio → Browser
│   │   └── tx_pipeline.py      # Browser → Radio
│   │
│   ├── stations/               # Schicht 2c: Stations Editor
│   │   ├── __init__.py
│   │   ├── router.py           # FastAPI Router (CRUD + EEPROM)
│   │   └── eeprom.py           # EEPROM Read/Write Logik
│   │
│   └── utils/
│       ├── __init__.py
│       └── logging.py          # Strukturiertes Logging
│
├── frontend/
│   ├── index.html              # Hauptseite (Radio Control)
│   ├── static/
│   │   ├── css/
│   │   │   ├── style.css       # Instrument-Panel Theme
│   │   │   └── stations.css    # Stations Editor Styles
│   │   └── js/
│   │       ├── app.js          # Main Entry, Module-Orchestrator
│   │       ├── control.js      # SocketIO Client (Buttons, Display, Status)
│   │       ├── audio.js        # RX Audio (WebSocket + Decode)
│   │       ├── tx_audio.js     # TX Audio (WebSocket + Encode)
│   │       ├── display.js      # Canvas LCD Renderer
│   │       ├── smeter.js       # S-Meter mit Decay
│   │       ├── stations.js     # Stations Editor (CRUD + Import/Export + Backups)
│   │       └── worklet.js      # AudioWorklet Processor
│   │
│   └── templates/
│       ├── login.html
│       ├── admin.html
│       ├── admin_logs.html
│       └── stations.html
│
├── docs/
│   ├── protocol.md             # Quansheng Serial Protocol Spec
│   ├── api.md                  # WebSocket API Spec
│   ├── quanshengdock-functions.md  # QuanshengDock Funktionsreferenz
│   └── PLAN.md                 # Dieses Dokument
│
└── logs/                       # Activity Logs
```

---

## Phasenplan – Aktueller Stand

### ✅ Phase 1 – Fundament: **ERLEDIGT**
- [x] Projektstruktur anlegen
- [x] Config-System (YAML + Loader)
- [x] Radio Layer: Serial Connection mit Auto-Reconnect
- [x] Radio Layer: Protocol Handler
- [x] Radio Layer: Display Parser (LCD-Bytes → Canvas-Daten)
- [x] FastAPI Basis-App mit Health-Check
- [x] `GET /api/status` liefert Radio-Status + Display-Daten

### ✅ Phase 2 – Control: **ERLEDIGT**
- [x] SocketIO Server (Buttons, Display-Updates, Status)
- [x] Frontend: Canvas LCD Renderer (aus V1 übernommen + verbessert)
- [x] Frontend: Button-Grid (UP, DOWN, MENU, EXIT, F, 0-9, *, #)
- [x] Frontend: Knob (Drehregler)
- [x] Frontend: SocketIO Client Modul
- [x] Auth: Session-basiertes Login + User-Verwaltung (statt JWT)
- [x] Admin-Page (User anlegen/bearbeiten/löschen)

### ✅ Phase 3 – Audio: **ERLEDIGT**
- [x] ScriptProcessorNode (statt AudioWorklet – läuft stabil)
- [x] Raw WebSocket `/audio/rx` (Radio → Browser)
- [x] Raw WebSocket `/audio/tx` (Browser → Radio)
- [x] Backend: RX Pipeline (ALSA → μ-law → WS)
- [x] Backend: TX Pipeline (WS → μ-law → ALSA)
- [x] Frontend: Audio Module (RX + TX separater WS)
- [x] PTT-Taste (Pointerdown/Pointerup → TX WS open/close)
- [ ] ~Adaptive Chunk-Größe~ (nicht benötigt, läuft stabil)

### ✅ Phase 4 – Polish: **ERLEDIGT**
- [x] S-Meter (RSSI → dBm → S-Unit, Decay)
- [x] PTT-Lock (nur 1 User gleichzeitig)
- [x] Admin-Page (User anlegen/bearbeiten/löschen)
- [x] Activity-Log (wer hat wann was gemacht)
- [x] Battery-Voltage Anzeige
- [x] Automatischer Reconnect (Frontend)
- [x] Mobile-optimiertes CSS
- [x] Systemd-Service File
- [x] README mit Setup-Anleitung
- [x] Session Timeout mit per-User-Einstellung (Admin-Panel)
- [x] Admin Logs Download

### ✅ Phase 5 – Nice-to-Have: **TEILWEISE ERLEDIGT**
- [x] **Spectrum Analyzer + Waterfall** (vorzeitig gebaut!)
- [x] **Stations Editor** – Kanäle aus EEPROM lesen/schreiben, CSV Import/Export, Backups
- [ ] PWA (Homescreen-Install)
- [ ] Multi-Radio Support

---

## Phase 6 – Neue Funktionen (Roadmap)

> Basierend auf der QuanshengDock-Funktionsreferenz (`docs/quanshengdock-functions.md`)

### 🔴 Hochpriorität
- [ ] **Preset Scanner** – scannt gespeicherte Kanäle mit Signalanzeige
- [ ] **Range Scanner** – Frequenzbereich scannen mit Bar-Graph
- [ ] **Auto Squelch** – automatische Rauschsperre
- [ ] **VOX** – sprachgesteuerte TX
- [ ] **1750 Hz Ton** – Repeater-Aufrufton senden
- [ ] **DTMF TX** – Tasten 0-9, A-D, *, # senden
- [ ] **Squelch-Stufen** – SQ-1 bis SQ-4 per UI wählbar

### 🟡 Mittlere Priorität
- [ ] **Mic Gain / Boost** – einstellbare Mikrofonverstärkung
- [ ] **Mic Level Indicator** – Pegelanzeige
- [ ] **Keyboard Shortcuts** – Web-Äquivalente zu QuanshengDock
- [ ] **AGC/RF Gain** Steuerung
- [ ] **TX Unlock** – Sicherheitsfeature, TX standardmäßig gesperrt
- [ ] **Scan-Logging** – CSV-Export
- [ ] **DTMF Decoding** – Anzeige empfangener DTMF-Töne

### 🟢 Niedrige Priorität
- [ ] **RepeaterBook Integration** – Repeater-Daten direkt importieren
- [ ] **Messenger** – Simple Messaging-Funktion
- [ ] **CAT Control** – virtueller serieller Port
- [ ] **Multi-VFO** – VFOs A/B/C/D umschaltbar
- [ ] **PWA** – Homescreen-Install
- [ ] **Multi-Radio Support**

### 🔧 Technische Verbesserungen (Tech Debt)
- [ ] **AudioWorklet Migration** – ScriptProcessorNode ist deprecated, auf AudioWorkletNode umziehen
- [ ] **Adaptive Chunk-Größe** – bei schlechter Verbindung automatisch anpassen

---

## Technologie-Stack

| Komponente | Wahl | Warum |
|-----------|------|-------|
| Backend | **FastAPI + Uvicorn** | Async, schnell, moderne Python-API |
| Control | **python-socketio** (async) | Bewährt, Reconnect, Namespaces |
| Audio Transport | **Raw WebSocket** (FastAPI WS) | Minimaler Overhead, kein Heartbeat-Konflikt |
| Audio Codec | **μ-law (G.711)** | Bewährt, keine Abhängigkeiten |
| Audio Capture | **ScriptProcessorNode** | Läuft stabil, Worklet-Migration geplant |
| Auth | **Session-basiert** (Starlette Sessions) | Einfach, Server-side, per-User Timeout |
| Config | **YAML** | Menschenlesbar, einfach |
| Frontend | **Vanilla JS + ES Modules** | Kein Build, keine Abhängigkeiten |
| Serial | **pyserial** | Standard |
| Audio I/O | **ALSA (subprocess)** | Pi-Standard |
| Logging | **structlog** oder **logging + JSON** | Strukturiert, durchsuchbar |
| Reverse Proxy | **Caddy** | HTTPS automatisch |
| User Storage | **users.json** | Simpel für wenige User |

---

## Was wir aus V1 übernehmen
- Canvas LCD Renderer (konzeptionell, sauberer rewrite)
- S-Meter Decay-Logik
- Knob-Interaktion
- Login/Admin-Konzept
- Quansheng Protocol Doku (als Grundlage für protocol.py)

## Was wir NICHT übernehmen
- Flask + threading
- Audio über SocketIO
- Monolith-App.js
- Hardcoded Config
- patch_*.py Hotfix-Dateien

---

## Deployment & Umgebung

| Was | Wert |
|-----|------|
| Backend | Python/FastAPI + Uvicorn |
| Reverse Proxy | Caddy (automatic HTTPS) |
| Firmware | QuanshengDock 0.32.21q |
| Repo | <https://github.com/3DFabrik/q-remote-v3> |

## Gegebenheiten ⚠️

- **HTTPS ist Pflicht** – Browser verlangen sicheren Kontext für `getUserMedia()` (Mikrofon). Caddy übernimmt das automatisch.
- **Dokumentation** – Jede Komponente wird dokumentiert (Docstrings + README + API-Docs). Code soll für andere verständlich sein.
- **Keine Secrets im Code** – Config-Templates mit Platzhaltern, echte Werte nur in der lokalen Deployment-Config.

## Entscheidungen ✅

1. **ALSA → subprocess** – `arecord`/`aplay` als subprocess. Simpler, weniger Abhängigkeiten, leichter zu debuggen.

2. **Zwei Audio-WebSockets** – `/audio/rx` (Radio→Browser, permanent) und `/audio/tx` (Browser→Radio, nur bei PTT offen). Sauber getrennt.

3. **PTT über Control-SocketIO** – PTT-Button → SocketIO Event → Backend aktiviert TX-Pfad → Browser öffnet TX-WS → Audio fließt. PTT loslassen → SocketIO Event → TX-WS wird geschlossen.

4. **Session-basiert statt JWT** – Starlette Session Middleware. Simpler, Server-side Timeout möglich, per-User konfigurierbar.

5. **UV-K5 zuerst, Hamlib später** – Radio-Layer hinter einem Interface. V3 implementiert den Quansheng-Adapter (Serial). Später kommt ein Hamlib-Adapter für andere Geräte.

---

## Changelog

| Datum | Was |
|-------|-----|
| 2025-xx-xx | Plan erstellt (Phasen 1-5) |
| 2026-06-11 | Phasen 1-5 als erledigt markiert, Phase 6 hinzugefügt |
| 2026-06-11 | Session Timeout Feature (per-User, Admin-Panel) |
| 2026-06-12 | Bugfix: Stations Editor nach Timeout-Feature kaputt (duplizierter JS-Code + Exception Handler) |
| 2026-06-12 | Favicon für Stations Editor, breiteres Layout (max-width 1400px) |
| 2026-06-12 | QuanshengDock Funktionsreferenz extrahiert (`docs/quanshengdock-functions.md`) |
| 2026-06-12 | PLAN.md aktualisiert: IST-Zustand, Phase 6 Roadmap |
