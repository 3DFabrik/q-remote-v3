"""Q-Remote V3 – FastAPI Application."""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from jinja2 import Environment, FileSystemLoader
from starlette.middleware.sessions import SessionMiddleware

from backend.config import load_config, get
from backend.utils.logging import setup_logging
from backend.control.socketio_server import init_radio, sio
import backend.control.socketio_server as _sio_mod
from backend.audio.rx_pipeline import RxPipeline
from backend.audio.tx_pipeline import TxPipeline
from backend.stations.router import stations_router
from backend.gpio import manager as gpio_manager
from backend.login_guard import (
    get_client_ip,
    check_login_allowed,
    record_login_failure,
    record_login_success,
    apply_login_delay,
    format_retry_message,
)
from backend.auth import (
    SECRET_KEY, USERS, load_users, save_users, log_activity,
    get_current_user, get_valid_user, get_valid_ws_user, is_admin, login_required, admin_required,
    touch_activity, clear_activity, check_timeout, parse_timeout, get_user_timeout_minutes,
    register_gpio_session, unregister_gpio_session, is_gpio_session_active,
    get_stale_gpio_sessions, DEFAULT_HEARTBEAT_MISS_SECONDS,
    establish_session, clear_session,
)

logger = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
TEMPLATES_DIR = FRONTEND_DIR / "templates"

rx_audio = RxPipeline()
tx_audio = TxPipeline()

jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=True,
    auto_reload=True,
)

NO_STORE_HEADERS = {"Cache-Control": "no-store, no-cache, must-revalidate"}


async def _gpio_session_watchdog_loop():
    """Turn off session-bound GPIO when heartbeats stop (connection lost)."""
    miss_seconds = float(get("auth.heartbeat_miss_seconds", DEFAULT_HEARTBEAT_MISS_SECONDS))
    interval = float(get("auth.heartbeat_check_interval", 30))
    logger.info(f"GPIO session watchdog started (check every {interval}s, miss after {miss_seconds}s)")
    while True:
        try:
            await asyncio.sleep(interval)
            for user in get_stale_gpio_sessions(miss_seconds):
                logger.warning(f"Session watchdog: no heartbeat from {user}")
                log_activity(user, "WATCHDOG_LOGOUT", f"no heartbeat for {int(miss_seconds)}s")
                unregister_gpio_session(user)
                gpio_manager.on_session_logout(user)
        except asyncio.CancelledError:
            logger.info("GPIO session watchdog stopped")
            raise
        except Exception as e:
            logger.error(f"GPIO session watchdog error: {e}", exc_info=True)


async def _radio_reconnect_loop():
    """Try to reopen the radio serial link when the USB device returns."""
    delay = float(get("radio.reconnect_delay", 3.0))
    logger.info(f"Radio reconnect watcher started (every {delay}s)")
    while True:
        try:
            await asyncio.sleep(delay)
            r = _sio_mod.radio
            if not r or r.connected:
                continue
            device = Path(get("radio.device", "/dev/ttyACM0"))
            if not device.exists():
                continue
            logger.info("Radio device %s is back — reconnecting", device)
            if r.reconnect():
                logger.info("Radio reconnected")
                await _sio_mod.sio.emit("radio_state", {"state": "connected"})
                loop = asyncio.get_event_loop()
                if not rx_audio._running:
                    rx_audio.start(loop)
                tx_audio.start()
                tx_audio.set_relay_targets(rx_audio._clients)
            else:
                logger.warning("Radio reconnect failed")
        except asyncio.CancelledError:
            logger.info("Radio reconnect watcher stopped")
            raise
        except Exception as e:
            logger.error(f"Radio reconnect watcher error: {e}", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_config()
    setup_logging()
    load_users()
    logger.info("Q-Remote V3 starting up")

    r = init_radio()
    connected = r.connect()
    if connected:
        logger.info("Radio connected")
        rx_audio.start(asyncio.get_event_loop())
        try:
            rx_audio.squelch_enabled = get("audio.squelch_enabled", True)
            rx_audio.squelch_threshold = get("audio.squelch_threshold", 300)
            logger.info(f"Squelch: enabled={rx_audio.squelch_enabled}, threshold={rx_audio.squelch_threshold}")
        except Exception as e:
            logger.warning(f"Could not load squelch config: {e}")
        tx_audio.start()
        tx_audio.set_relay_targets(rx_audio._clients)
    else:
        logger.warning("Radio not available")

    # GPIO initialization (fail-safe: all pins OFF, independent of radio)
    try:
        await gpio_manager.initialize()
    except Exception as e:
        logger.warning(f"GPIO init failed (non-Pi or no gpiozero?): {e}")

    watchdog_task = asyncio.create_task(_gpio_session_watchdog_loop())
    radio_reconnect_task = asyncio.create_task(_radio_reconnect_loop())

    yield

    radio_reconnect_task.cancel()
    try:
        await radio_reconnect_task
    except asyncio.CancelledError:
        pass

    watchdog_task.cancel()
    try:
        await watchdog_task
    except asyncio.CancelledError:
        pass

    gpio_manager.cleanup()
    logger.info("Shutting down...")
    rx_audio.stop()
    tx_audio.stop()
    r.disconnect()
    logger.info("Goodbye!")


app = FastAPI(title="Q-Remote V3", version="3.0.0", lifespan=lifespan)

app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code in (401, 403):
        # API routes should get JSON 401, not a redirect
        if request.url.path.startswith(("/api/", "/stations/api/")):
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        return RedirectResponse(url="/login", status_code=303)
    return HTMLResponse(f"<h1>{exc.status_code}</h1><p>{exc.detail}</p>", status_code=exc.status_code)


app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

app.include_router(stations_router)
from backend.gpio import router as gpio_router
app.include_router(gpio_router)

import socketio as sio_module
asgi_app = sio_module.ASGIApp(sio, other_asgi_app=app, socketio_path='socket.io')


# ─── Health / Status (no auth) ────────────────────────────────────

@app.get("/api/health")
async def health_check():
    if _sio_mod.radio and _sio_mod.radio.connected:
        return {"status": "ok", "radio": "connected", "version": "3.0.0"}
    return {"status": "degraded", "radio": "disconnected", "version": "3.0.0"}


@app.get("/api/status")
async def get_status():
    if not _sio_mod.radio or not _sio_mod.radio.connected:
        return {"state": "disconnected"}
    return {"state": "connected"}


# ─── Heartbeat & Tab-Close ─────────────────────────────────────────

@app.post("/api/heartbeat")
async def heartbeat(request: Request):
    """Keep session alive while tab is open. Returns session status."""
    user = get_valid_user(request)
    if not user:
        clear_session(request)
        return JSONResponse({"status": "expired"}, status_code=401)
    # Re-register after watchdog drop (e.g. WiFi was briefly down)
    if not is_gpio_session_active(user):
        register_gpio_session(user)
        gpio_manager.on_session_login(user)
    touch_activity(user)
    timeout_min = get_user_timeout_minutes(user) if user else 0
    return {"status": "ok", "timeout_minutes": timeout_min}


@app.post("/api/close")
async def tab_close(request: Request):
    """Called on beforeunload – logout user when tab closes."""
    user = request.session.get("user")
    request.session.clear()
    if user:
        unregister_gpio_session(user)
        log_activity(user, "TAB_CLOSE_LOGOUT")
        gpio_manager.on_session_logout(user)
    return {"status": "ok"}


# ─── Auth Routes ───────────────────────────────────────────────────

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if get_valid_user(request):
        return RedirectResponse(url="/", status_code=303)
    return HTMLResponse(
        jinja_env.get_template("login.html").render(request=request),
        headers=NO_STORE_HEADERS,
    )


@app.post("/login")
async def login_submit(request: Request):
    client_ip = get_client_ip(request)
    allowed, retry_after = check_login_allowed(client_ip)
    if not allowed:
        await apply_login_delay()
        log_activity("-", "LOGIN_BLOCKED", f"ip={client_ip} retry={retry_after}s")
        return HTMLResponse(
            jinja_env.get_template("login.html").render(
                request=request,
                error=format_retry_message(retry_after),
            ),
            headers=NO_STORE_HEADERS,
            status_code=429,
        )

    form = await request.form()
    username = (form.get("username") or "").strip()
    password = form.get("password") or ""
    user_data = USERS.get(username, {})
    if user_data.get("password") == password:
        record_login_success(client_ip)
        establish_session(request, username)
        log_activity(username, "LOGIN", f"ip={client_ip}")
        gpio_manager.on_session_login(username)
        return RedirectResponse(url="/", status_code=303)

    record_login_failure(client_ip)
    await apply_login_delay()
    log_activity(username or "-", "LOGIN_FAILED", f"ip={client_ip}")
    return HTMLResponse(
        jinja_env.get_template("login.html").render(
            request=request,
            error="Invalid callsign or password.",
        ),
        headers=NO_STORE_HEADERS,
        status_code=401,
    )


@app.get("/logout")
async def logout(request: Request):
    user = request.session.get("user")
    request.session.clear()
    if user:
        unregister_gpio_session(user)
        log_activity(user, "LOGOUT")
        gpio_manager.on_session_logout(user)
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
        timeout = form.get("new_timeout", "02:00").strip()
        # Validate timeout format
        try:
            parse_timeout(timeout)
        except Exception:
            timeout = "02:00"
        if username and password and username not in USERS:
            USERS[username] = {"password": password, "admin": admin, "timeout": timeout}
            save_users()
            log_activity(get_current_user(request), "ADD_USER", f"user={username}")

    elif action == "edit":
        username = form.get("edit_username", "").strip()
        password = form.get("edit_password", "").strip()
        admin = form.get("edit_admin") == "on"
        timeout = form.get("edit_timeout", "").strip()
        if username in USERS:
            if password:
                USERS[username]["password"] = password
            USERS[username]["admin"] = admin
            if timeout:
                try:
                    parse_timeout(timeout)
                    USERS[username]["timeout"] = timeout
                except Exception:
                    pass
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
async def serve_index(request: Request, user: str = Depends(login_required)):
    html = (FRONTEND_DIR / "index.html").read_text()
    is_user_admin = USERS.get(user, {}).get("admin", False)
    timeout_min = get_user_timeout_minutes(user)
    user_json = json.dumps({"name": user, "admin": is_user_admin, "timeout_minutes": timeout_min})
    inject = f'<script>window.CURRENT_USER = {user_json};</script>'
    html = html.replace("</head>", inject + "</head>", 1)
    return HTMLResponse(content=html, headers=NO_STORE_HEADERS)


# ─── Audio WebSockets (auth required) ─────────────────────────────

@app.websocket("/audio/rx")
async def audio_rx_ws(websocket: WebSocket):
    user = get_valid_ws_user(websocket)
    if not user:
        await websocket.close(code=4001, reason="Not authenticated")
        return
    touch_activity(user)

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
    user = get_valid_ws_user(websocket)
    if not user:
        await websocket.close(code=4001, reason="Not authenticated")
        return
    touch_activity(user)

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
