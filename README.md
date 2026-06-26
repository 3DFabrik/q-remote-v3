# Q-Remote V3 🤖📻

Web-based remote control for Quansheng UV-K5 ham radio with QuanshengDock firmware. Access your radio from anywhere through the browser – live display, complete control, and a full channel editor.

> **Special thanks to [Nic Sure](https://github.com/nicsure) for the amazing [QuanshengDock](https://github.com/nicsure/QuanshengDock) project – the firmware, protocol documentation, and C# reference implementation that made Q-Remote V3 possible. Without this foundational work, none of this would exist. 🙏

![Q-Remote V3 Screenshot](docs/screenshot.jpg)

*Login panel:*

![Login Panel](docs/screenshot-login.jpg)

*TX mode with MOD meter (mic level in dBFS):*

![TX MOD Meter](docs/screenshot-tx-mod.jpg)

*Stations editor – full channel management with squelch control:*

![Stations Editor](docs/screenshot-stations.jpg)

*Admin panel – user management with per-user session timeout:*

![Admin Panel](docs/screenshot-admin.jpg)

## Features

### ✅ Working

**Radio Control**
- **Live CRT Display** – Radio LCD rendered in real-time on HTML5 Canvas with anti-flicker
- **RX Audio** – Listen to incoming radio audio in your browser (μ-law codec, 8kHz)
- **TX Audio** – Transmit through your browser's microphone (μ-law, click-free)
- **Full Button Control** – 4×4 button grid matching the UV-K5 layout with correct labels
- **PTT (Push-to-Talk)** – Hold to transmit, with TX lock for multi-user safety
- **Analog S-Meter** – Real-time signal strength needle with continuous dBm mapping
- **Mic Modulation Meter** – dBFS level display during transmit (MOD scale)
- **Squelch Control** – Adjustable squelch threshold with audio gating

**Stations Editor**
- **Full Channel Management** – Read/write all 200 channels directly from the radio EEPROM
- **Inline Editing** – Edit any channel parameter directly in the data grid
- **CSV Import/Export** – Import and export channel lists as CSV files
- **Backup & Restore** – Automatic backups before every write, restore any previous snapshot
- **Empty Channel Handling** – Empty channels are correctly hidden from the radio's channel scan
- **Auto-Reset After Write** – Radio MCU resets automatically after EEPROM write to reload channel data

**Multi-User & Security**
- **Login System** – Multi-user authentication with admin panel
- **User Management** – Add/remove users, set admin privileges
- **Per-User Session Timeout** – Configurable inactivity timeout (HH:MM) per user via admin panel
  - Default: 2 hours, set to `00:00` for unlimited sessions
  - Automatic logout on timeout with redirect to login screen
  - Tab/window close triggers immediate logout
  - Activity tracking across all pages (radio control, station editor, admin panel)
- **PTT Locking** – Only one user can transmit at a time
- **Activity Logging** – Login, logout, timeout, PTT events tracked in activity log

### 🔧 In Progress
- Spectrum bandscope (SCAN protocol 0x0808 working, UI pending)

## Architecture

| Layer | Technology |
|-------|-----------|
| Frontend | Vanilla JS + ES Modules, HTML5 Canvas |
| Real-time Control | Socket.IO (async WebSocket) |
| Audio Transport | Raw WebSocket + G.711 μ-law codec |
| Backend | Python FastAPI + Uvicorn |
| Radio Protocol | Quansheng UV-K5 Serial Protocol (QuanshengDock firmware) |
| Sound Card | AIOC (All-In-One-Cable) for ALSA audio I/O |

## Hardware

- **Radio:** Quansheng UV-K5 with [QuanshengDock Firmware](https://github.com/nicsure/quansheng-dock-fw) (v0.32.21q)
- **Sound Card:** AIOC (All-In-One-Cable) – USB audio + serial in one device
- **Server:** Raspberry Pi behind Caddy reverse proxy (automatic HTTPS)

## Quick Start

### Automated install (Raspberry Pi)

```bash
git clone https://github.com/3DFabrik/q-remote-v3.git
cd q-remote-v3
chmod +x scripts/install.sh scripts/uninstall.sh
sudo ./scripts/install.sh
```

This will:

- Install system packages (`python3`, `venv`, `alsa-utils`, …)
- Create a Python virtualenv and install all pip dependencies (including `gpiozero`)
- Add the service user to `gpio`, `dialout`, and `audio` groups
- Create `config.local.yaml` from the example (if missing)
- Prompt for the first admin account (`users.json`) if none exists
- Install and start a **systemd** service (`q-remote`)

```bash
# Service management
sudo systemctl status q-remote
sudo journalctl -u q-remote -f
sudo ./scripts/uninstall.sh   # remove service only (keeps data)
```

Options: `--user pi`, `--port 8080`, `--deps-only` (no systemd), `--no-start`

### Manual install

```bash
git clone https://github.com/3DFabrik/q-remote-v3.git
cd q-remote-v3

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp config.local.yaml.example config.local.yaml
# Edit config.local.yaml (radio device, audio, …)
# Create users.json or log in once via admin after first manual user setup

uvicorn backend.main:asgi_app --host 0.0.0.0 --port 8080
```

Open `https://your-pi` in your browser (via reverse proxy). **HTTPS is required** for microphone access.

## Requirements

- Raspberry Pi OS (or Linux) with Python 3.11+
- `alsa-utils` (`arecord` / `aplay`) for radio audio
- Modern browser (Chrome, Firefox, Edge, Safari)
- Quansheng UV-K5 with QuanshengDock firmware
- AIOC or similar USB serial + audio interface
- HTTPS (via Caddy, Nginx, or similar reverse proxy)

## Project Structure

```
q-remote-v3/
├── backend/
│   ├── app.py              # FastAPI app, routes, audio WebSockets, heartbeat API
│   ├── config.py           # YAML config loader
│   ├── auth.py             # User auth, session management, timeout tracking
│   ├── radio/
│   │   ├── connection.py   # Serial protocol, radio communication, EEPROM access
│   │   ├── protocol.py     # Packet building & parsing (XOR + CRC16)
│   │   └── lcd.py          # LCD display state, RSSI parsing
│   ├── audio/
│   │   ├── rx_pipeline.py  # ALSA capture → μ-law → WebSocket
│   │   └── tx_pipeline.py  # WebSocket → μ-law decode → ALSA playback
│   ├── control/
│   │   └── socketio_server.py  # SocketIO events, PTT, keys, RSSI
│   └── stations/
│       ├── eeprom.py       # EEPROM parser/packer (200 channels, 3 regions)
│       └── router.py       # Stations Editor API (read/write/backup/restore/CSV)
├── frontend/
│   ├── index.html          # Main remote control UI
│   ├── static/
│   │   ├── css/style.css   # Instrument-panel theme
│   │   └── js/
│   │       ├── app.js      # Main app, audio toggle, PTT, session heartbeat
│   │       ├── control.js  # SocketIO client
│   │       ├── display.js  # Canvas LCD renderer
│   │       ├── smeter.js   # Analog S-Meter + MOD meter
│   │       ├── audio.js    # RX audio (μ-law decode)
│   │       ├── tx_audio.js # TX audio (mic → μ-law encode)
│   │       └── stations.js # Stations editor frontend + session management
│   └── templates/
│       ├── admin.html      # User management + timeout settings
│       ├── admin_logs.html # Activity logs
│       ├── login.html      # Login page
│       └── stations.html   # Channel editor
├── docs/
│   └── EEPROM-STRUCTURE.md # EEPROM byte layout documentation
├── scripts/
│   ├── install.sh          # Pi installer (deps + systemd)
│   ├── uninstall.sh        # Remove systemd service
│   └── q-remote.service.in # systemd unit template
├── config.yaml             # Default settings
└── config.local.yaml.example
```

## License

MIT

---

Made by Norbot 🤖 & DF7ZZ
