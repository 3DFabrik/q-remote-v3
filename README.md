# Q-Remote V3

Web-based remote control for Quansheng UV-K5 ham radio. Access your radio from anywhere through the browser – audio in both directions, live display, full control.

## What it does

- **Live Display** – Radio LCD rendered in real-time on canvas
- **RX Audio** – Listen to radio audio from your browser
- **TX Audio** – Transmit through your browser's microphone
- **Full Control** – All buttons, knob, menu navigation
- **S-Meter** – Real-time signal strength display
- **Multi-User** – Multiple listeners, one TX lock

## Architecture

| Layer | Technology |
|-------|-----------|
| Frontend | Vanilla JS + ES Modules, Canvas |
| Control | SocketIO (async) |
| Audio | Raw WebSocket + μ-law codec |
| Backend | FastAPI + Uvicorn |
| Radio | Serial Protocol (UV-K5), later Hamlib for other radios |

See [docs/PLAN.md](docs/PLAN.md) for the full plan and architecture details.

## Requirements

- **HTTPS mandatory** – Required for microphone access (`getUserMedia`)
- Modern browser (Chrome, Firefox, Edge, Safari)
- Raspberry Pi (or any Linux box) with serial connection to radio

## Quick Start

```bash
# Coming soon - Phase 1 in progress
```

## Hardware

- Quansheng UV-K5 (primary target)
- Future: Any radio supported by Hamlib

## License

MIT

## Deployment

Runs on Raspberry Pi (HamPi) behind Caddy reverse proxy for automatic HTTPS.

---

Built with ☕ and 🤖 by [3DFabrik](https://github.com/3DFabrik)
