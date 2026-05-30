"""Q-Remote V3 – FastAPI Application."""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader
from starlette.middleware.sessions import SessionMiddleware

from backend.config import load_config, get
from backend.utils.logging import setup_logging
from backend.control.socketio_server import init_radio, radio, sio
from backend.audio.rx_pipeline import RxPipeline
from backend.audio.tx_pipeline import TxPipeline
from backend.auth import (
    SECRET_KEY, USERS, load_users, save_users, log_activity,
    get_current_user, get_ws_user, is_admin, login_required, admin_required,
)

logger = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
TEMPLATES_DIR = FRONTEND_DIR / "templates"

rx_audio = RxPipeline()
tx_audio = TxPipeline()

jinja_env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_config()
    setup_logging()
    # Refresh users from file on startup
    load_users()
    logger.info("Q-Remote V3 starting up")

    r = init_radio()
    connected = r.connect()  # blocking V1-style connect
    if connected:
        logger.info("Radio connected")
        # Start RX audio pipeline
        rx_audio.start(asyncio.get_event_loop())
        # Load squelch settings from config
        try:
            rx_audio.squelch_enabled = get("audio.squelch_enabled", True)
            rx_audio.squelch_threshold = get("audio.squelch_threshold", 300)
            logger.info(f"Squelch: enabled={rx_audio.squelch_enabled}, threshold={rx_audio.squelch_threshold}")
        except Exception as e:
            logger.warning(f"Could not load squelch config: {e}")
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

# Session middleware for cookie-based auth
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code in (401, 403):
        return RedirectResponse(url="/login", status_code=303)
    return HTMLResponse(f"<h1>{exc.status_code}</h1><p>{exc.detail}</p>", status_code=exc.status_code)

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

import socketio as sio_module
asgi_app = sio_module.ASGIApp(sio, other_asgi_app=app, socketio_path='socket.io')


# ─── Health / Status (no auth) ────────────────────────────────────

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


# ─── Auth Routes ───────────────────────────────────────────────────

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/", status_code=303)
    return HTMLResponse(jinja_env.get_template("login.html").render(request=request))


@app.post("/login")
async def login_submit(request: Request):
    form = await request.form()
    username = form.get("username", "")
    password = form.get("password", "")
    user_data = USERS.get(username, {})
    if user_data.get("password") == password:
        request.session["user"] = username
        log_activity(username, "LOGIN")
        return RedirectResponse(url="/", status_code=303)
    return HTMLResponse(jinja_env.get_template("login.html").render(request=request, error="Ungültige Anmeldedaten"))


@app.get("/logout")
async def logout(request: Request):
    user = request.session.pop("user", None)
    if user:
        log_activity(user, "LOGOUT")
    return RedirectResponse(url="/login", status_code=303)


# ─── Admin Routes ──────────────────────────────────────────────────

@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request, _=Depends(admin_required)):
    return HTMLResponse(jinja_env.get_template("admin.html").render(
        request=request, users=USERS, current_user=get_current_user(request),
        squelch_enabled=rx_audio.squelch_enabled,
        squelch_threshold=rx_audio.squelch_threshold,
    ))


@app.post("/admin/users")
async def admin_update_users(request: Request, _=Depends(admin_required)):
    form = await request.form()
    action = form.get("action")

    if action == "add":
        username = form.get("new_username", "").strip()
        password = form.get("new_password", "").strip()
        admin = form.get("new_admin") == "on"
        if username and password and username not in USERS:
            USERS[username] = {"password": password, "admin": admin}
            save_users()
            log_activity(get_current_user(request), "ADD_USER", f"user={username}")

    elif action == "edit":
        username = form.get("edit_username", "").strip()
        password = form.get("edit_password", "").strip()
        admin = form.get("edit_admin") == "on"
        if username in USERS:
            if password:
                USERS[username]["password"] = password
            USERS[username]["admin"] = admin
            save_users()
            log_activity(get_current_user(request), "EDIT_USER", f"user={username}")

    elif action == "delete":
        username = form.get("del_username", "").strip()
        if username in USERS and username != get_current_user(request):
            del USERS[username]
            save_users()
            log_activity(get_current_user(request), "DELETE_USER", f"user={username}")

    return RedirectResponse(url="/admin", status_code=303)


@app.get("/admin/logs", response_class=HTMLResponse)
async def admin_logs(request: Request, _=Depends(admin_required)):
    log_file = Path(__file__).parent.parent / "logs" / "activity.log"
    entries = []
    if log_file.exists():
        lines = log_file.read_text().splitlines()
        entries = list(reversed(lines[-200:]))
    return HTMLResponse(jinja_env.get_template("admin_logs.html").render(request=request, entries=entries))


@app.get("/admin/logs/download")
async def admin_logs_download(request: Request, _=Depends(admin_required)):
    from fastapi.responses import FileResponse
    log_file = Path(__file__).parent.parent / "logs" / "activity.log"
    if log_file.exists():
        return FileResponse(log_file, filename="activity.log")
    return HTMLResponse("No log file", status_code=404)


# ─── Main Page (auth required) ────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def serve_index(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    html = (FRONTEND_DIR / "index.html").read_text()
    is_user_admin = USERS.get(user, {}).get("admin", False)
    user_json = json.dumps({"name": user, "admin": is_user_admin})
    inject = f'<script>window.CURRENT_USER = {user_json};</script>'
    html = html.replace("</head>", inject + "</head>", 1)
    return HTMLResponse(content=html)


# ─── Audio WebSockets (auth required) ─────────────────────────────

@app.websocket("/audio/rx")
async def audio_rx_ws(websocket: WebSocket):
    """Raw WebSocket for RX audio stream (μ-law bytes)."""
    # Auth check: read session cookie
    user = get_ws_user(websocket)
    if not user:
        await websocket.close(code=4001, reason="Not authenticated")
        return

    await websocket.accept()
    logger.info(f"RX audio WebSocket client connected (user={user})")
    rx_audio.add_client(websocket)
    try:
        while True:
            await websocket.receive_text()
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
    # Auth check: read session cookie
    user = get_ws_user(websocket)
    if not user:
        await websocket.close(code=4001, reason="Not authenticated")
        return

    await websocket.accept()
    logger.info(f"TX audio WebSocket client connected (user={user})")
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


# ─── Squelch Settings API ──────────────────────────────────────────

@app.get("/api/squelch")
async def get_squelch(request: Request, _=Depends(admin_required)):
    return {
        "enabled": rx_audio.squelch_enabled,
        "threshold": rx_audio.squelch_threshold,
    }


@app.post("/api/squelch")
async def set_squelch(request: Request, _=Depends(admin_required)):
    from backend.config import load_config, get
    form = await request.form()
    enabled = form.get("squelch_enabled") == "on"
    threshold = int(form.get("squelch_threshold", "300"))
    threshold = max(10, min(10000, threshold))
    rx_audio.squelch_enabled = enabled
    rx_audio.squelch_threshold = threshold
    # Persist to config
    cfg_path = Path(__file__).parent.parent / "config.local.yaml"
    import yaml
    cfg = {}
    if cfg_path.exists():
        cfg = yaml.safe_load(cfg_path.read_text()) or {}
    cfg.setdefault("audio", {})["squelch_enabled"] = enabled
    cfg["audio"]["squelch_threshold"] = threshold
    cfg_path.write_text(yaml.dump(cfg, default_flow_style=False))
    log_activity(get_current_user(request), "SQUELCH", f"enabled={enabled} threshold={threshold}")
    return RedirectResponse(url="/admin", status_code=303)


app.mount("/static", StaticFiles(directory=FRONTEND_DIR / "static"), name="static")
