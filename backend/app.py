"""Q-Remote V3 – FastAPI Application.

Web-based remote control for Quansheng UV-K5 ham radio.
Runs behind Caddy reverse proxy for automatic HTTPS.

Usage:
    uvicorn backend.app:app --host 0.0.0.0 --port 8080
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from pathlib import Path

from backend.config import load_config, get
from backend.utils.logging import setup_logging
from backend.radio.connection import QuanshengAdapter
from backend.radio.adapter import RadioState

logger = logging.getLogger(__name__)

# ─── Globals ──────────────────────────────────────────────────────

radio: QuanshengAdapter | None = None


# ─── Lifespan ─────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    global radio
    
    # Load config
    config = load_config()
    setup_logging()
    
    logger.info("Q-Remote V3 starting up")
    logger.info(f"Radio device: {get('radio.device')}")
    
    # Connect to radio
    radio = QuanshengAdapter()
    connected = await radio.connect()
    if connected:
        logger.info("Radio connected")
    else:
        logger.warning("Radio not available - will auto-reconnect")
    
    yield
    
    # Shutdown
    logger.info("Shutting down...")
    if radio:
        await radio.disconnect()
    logger.info("Goodbye!")


# ─── App ──────────────────────────────────────────────────────────

app = FastAPI(
    title="Q-Remote V3",
    description="Web-based remote control for Quansheng UV-K5",
    version="3.0.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=get("server.cors_origins", ["*"]) if False else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── REST API ─────────────────────────────────────────────────────

@app.get("/api/status")
async def get_status():
    """Get current radio status and info."""
    if not radio:
        return JSONResponse({"state": "unavailable"}, status_code=503)
    
    info = await radio.get_info()
    return {
        "state": info.state.value,
        "frequency_hz": info.frequency_hz,
        "battery_voltage": info.battery_voltage,
        "rssi_dbm": round(info.rssi_dbm, 1),
        "s_unit": info.s_unit,
        "is_transmitting": info.is_transmitting,
    }


@app.get("/api/health")
async def health_check():
    """Simple health check endpoint."""
    state = radio.state.value if radio else "unavailable"
    return {
        "status": "ok" if state == "connected" else "degraded",
        "radio": state,
        "version": "3.0.0",
    }


@app.post("/api/key/{keycode}")
async def send_key(keycode: int):
    """Send a key press to the radio.
    
    Key codes: 0-9 = digits, 10-12 = func keys, 13=Menu, 14=Up, 15=Down, 16=PTT, 19=Exit
    """
    if not radio or radio.state != RadioState.CONNECTED:
        return JSONResponse({"error": "Radio not connected"}, status_code=503)
    
    if keycode < 0 or keycode > 19:
        return JSONResponse({"error": "Invalid keycode"}, status_code=400)
    
    await radio.send_key(keycode)
    return {"ok": True, "keycode": keycode}


@app.post("/api/ptt/{active}")
async def set_ptt(active: bool):
    """Engage (true) or release (false) PTT."""
    if not radio or radio.state != RadioState.CONNECTED:
        return JSONResponse({"error": "Radio not connected"}, status_code=503)
    
    await radio.set_ptt(active)
    return {"ok": True, "ptt": active}


@app.post("/api/rssi")
async def request_rssi():
    """Request an RSSI reading from the radio."""
    if not radio or radio.state != RadioState.CONNECTED:
        return JSONResponse({"error": "Radio not connected"}, status_code=503)
    
    dbm = await radio.get_rssi()
    return {"rssi_dbm": round(dbm, 1)}


# ─── Static Files ─────────────────────────────────────────────────

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

app.mount("/static", StaticFiles(directory=FRONTEND_DIR / "static"), name="static")


@app.get("/", response_class=HTMLResponse)
async def serve_index():
    """Serve the main frontend page."""
    index_path = FRONTEND_DIR / "index.html"
    if index_path.exists():
        return index_path.read_text()
    return HTMLResponse("<h1>Q-Remote V3</h1><p>Frontend not built yet.</p>")
