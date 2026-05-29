"""Q-Remote V3 – FastAPI Application."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from pathlib import Path

from backend.config import load_config, get
from backend.utils.logging import setup_logging
from backend.control.socketio_server import init_radio, radio, sio
from backend.audio.rx_pipeline import RxPipeline
from backend.audio.tx_pipeline import TxPipeline

logger = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

rx_audio = RxPipeline()
tx_audio = TxPipeline()


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_config()
    setup_logging()
    logger.info("Q-Remote V3 starting up")

    r = init_radio()
    connected = r.connect()  # blocking V1-style connect
    if connected:
        logger.info("Radio connected")
        # Start RX audio pipeline
        rx_audio.start(asyncio.get_event_loop())
        tx_audio.start()
    else:
        logger.warning("Radio not available")

    yield

    logger.info("Shutting down...")
    rx_audio.stop()
    tx_audio.stop()
    r.disconnect()
    logger.info("Goodbye!")


app = FastAPI(title="Q-Remote V3", version="3.0.0", lifespan=lifespan)

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

import socketio as sio_module
asgi_app = sio_module.ASGIApp(sio, other_asgi_app=app, socketio_path='socket.io')


@app.get("/api/health")
async def health_check():
    if radio and radio.connected:
        return {"status": "ok", "radio": "connected", "version": "3.0.0"}
    return {"status": "degraded", "radio": "disconnected", "version": "3.0.0"}


@app.get("/api/status")
async def get_status():
    if not radio or not radio.connected:
        return {"state": "disconnected"}
    return {"state": "connected"}


@app.websocket("/audio/rx")
async def audio_rx_ws(websocket: WebSocket):
    """Raw WebSocket for RX audio stream (μ-law bytes)."""
    await websocket.accept()
    logger.info(f"RX audio WebSocket client connected")
    rx_audio.add_client(websocket)
    try:
        # Keep connection alive – client just receives
        while True:
            await websocket.receive_text()  # Wait for close/disconnect
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug(f"RX audio WS error: {e}")
    finally:
        rx_audio.remove_client(websocket)
        logger.info("RX audio WebSocket client disconnected")


@app.websocket("/audio/tx")
async def audio_tx_ws(websocket: WebSocket):
    """Raw WebSocket for TX audio stream (browser mic → μ-law → aplay)."""
    await websocket.accept()
    logger.info("TX audio WebSocket client connected")
    await tx_audio.add_client(websocket)
    try:
        while True:
            data = await websocket.receive_bytes()
            await tx_audio.handle_audio(websocket, data)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug(f"TX audio WS error: {e}")
    finally:
        await tx_audio.remove_client(websocket)
        logger.info("TX audio WebSocket client disconnected")


app.mount("/static", StaticFiles(directory=FRONTEND_DIR / "static"), name="static")


@app.get("/", response_class=HTMLResponse)
async def serve_index():
    return (FRONTEND_DIR / "index.html").read_text()
