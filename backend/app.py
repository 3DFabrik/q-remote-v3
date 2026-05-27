"""Q-Remote V3 – FastAPI Application.

Web-based remote control for Quansheng UV-K5 ham radio.
Runs behind Caddy reverse proxy for automatic HTTPS.

Usage:
    uvicorn backend.app:app --host 0.0.0.0 --port 8080
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from pathlib import Path

from backend.config import load_config, get
from backend.utils.logging import setup_logging
from backend.radio.connection import QuanshengAdapter
from backend.radio.adapter import RadioState
from backend.control.socketio_server import set_radio, get_sio_app

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
    
    # Wire up SocketIO with radio callbacks
    set_radio(radio)
    
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
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount SocketIO
socket_app = get_sio_app()
app.mount('/ws', socket_app)


# ─── REST API ─────────────────────────────────────────────────────

@app.get("/api/status")
async def get_status():
    """Get current radio status and info."""
    if not radio:
        return {"state": "unavailable"}
    
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
