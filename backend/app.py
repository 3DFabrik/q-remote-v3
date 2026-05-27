"""Q-Remote V3 – FastAPI Application."""

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
from backend.control.socketio_server import init_radio, radio, sio

logger = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_config()
    setup_logging()
    logger.info("Q-Remote V3 starting up")

    r = init_radio()
    connected = r.connect()  # blocking V1-style connect
    if connected:
        logger.info("Radio connected")
    else:
        logger.warning("Radio not available")

    yield

    logger.info("Shutting down...")
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


app.mount("/static", StaticFiles(directory=FRONTEND_DIR / "static"), name="static")


@app.get("/", response_class=HTMLResponse)
async def serve_index():
    return (FRONTEND_DIR / "index.html").read_text()
