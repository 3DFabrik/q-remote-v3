"""Q-Remote V3 – Authentication module (session-based, Starlette sessions)."""

import json
import logging
import time
from pathlib import Path
from datetime import datetime
from typing import Optional

from fastapi import Request, Depends, HTTPException
from starlette.responses import RedirectResponse

# ─── Config ────────────────────────────────────────────────────────

SECRET_KEY = "q-remo…e-me"
USER_FILE = Path(__file__).parent.parent / "users.json"
LOG_DIR = Path(__file__).parent.parent / "logs"

USERS: dict = {}

# ─── Session Timeout Tracking ──────────────────────────────────────

_session_activity: dict[str, float] = {}  # username -> last activity timestamp (epoch)
_logged_in_users: set[str] = set()        # users with active GPIO session tracking

DEFAULT_TIMEOUT_MINUTES = 120  # 2 hours default
DEFAULT_HEARTBEAT_MISS_SECONDS = 90  # ~1.5× frontend heartbeat interval (60s)

activity_log = logging.getLogger("activity")


def _init_activity_log():
    LOG_DIR.mkdir(exist_ok=True)
    handler = logging.FileHandler(LOG_DIR / "activity.log")
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    activity_log.addHandler(handler)
    activity_log.setLevel(logging.INFO)


def log_activity(user: str, action: str, details: str = ""):
    activity_log.info(f"[{user}] {action} {details}")


# ─── User Management ───────────────────────────────────────────────


def load_users():
    global USERS
    if USER_FILE.exists():
        with open(USER_FILE) as f:
            raw = json.load(f)
            for k, v in raw.items():
                if isinstance(v, str):
                    USERS[k] = {"password": v, "admin": False}
                else:
                    USERS[k] = v
    else:
        USERS = {}
    # Ensure timeout field exists for all users (default: 02:00 = 2 hours)
    for username, data in USERS.items():
        if "timeout" not in data:
            data["timeout"] = "02:00"


def save_users():
    with open(USER_FILE, "w") as f:
        json.dump(USERS, f, indent=4)


def get_current_user(request: Request) -> Optional[str]:
    """Get the currently logged-in username from the session, or None."""
    return request.session.get("user")


def get_ws_user(websocket) -> Optional[str]:
    """Get the currently logged-in username from a WebSocket session scope."""
    session = websocket.scope.get("session", {})
    return session.get("user")


def is_admin(request: Request) -> bool:
    """Check if the current user is an admin."""
    user = get_current_user(request)
    return bool(user and USERS.get(user, {}).get("admin", False))


# ─── Timeout Helpers ───────────────────────────────────────────────


def parse_timeout(timeout_str: str) -> int:
    """Parse 'HH:MM' timeout string to minutes. Returns 0 for no timeout."""
    if not timeout_str or timeout_str == "00:00":
        return 0
    try:
        parts = timeout_str.split(":")
        return int(parts[0]) * 60 + int(parts[1])
    except (ValueError, IndexError):
        return DEFAULT_TIMEOUT_MINUTES


def get_user_timeout_minutes(username: str) -> int:
    """Get timeout in minutes for a user. 0 = no timeout."""
    user_data = USERS.get(username, {})
    timeout_str = user_data.get("timeout", "02:00")
    return parse_timeout(timeout_str)


def touch_activity(username: str):
    """Update last activity timestamp for a user."""
    if username:
        _session_activity[username] = time.time()


def check_timeout(username: str) -> bool:
    """Check if user's session has timed out. Returns True if timed out."""
    if not username:
        return True
    timeout_minutes = get_user_timeout_minutes(username)
    if timeout_minutes == 0:
        return False  # No timeout
    last = _session_activity.get(username)
    if last is None:
        return False  # No activity recorded yet (fresh login)
    elapsed = time.time() - last
    return elapsed > (timeout_minutes * 60)


def clear_activity(username: str):
    """Remove activity tracking for a user (on logout)."""
    _session_activity.pop(username, None)


def register_gpio_session(username: str):
    """Mark user as logged in for GPIO session-bound watchdog tracking."""
    if username:
        _logged_in_users.add(username)
        touch_activity(username)


def unregister_gpio_session(username: str):
    """Remove user from GPIO session tracking."""
    if username:
        _logged_in_users.discard(username)
        clear_activity(username)


def is_gpio_session_active(username: str) -> bool:
    return username in _logged_in_users


def get_stale_gpio_sessions(miss_seconds: float) -> list[str]:
    """Users with no recent heartbeat/activity (connection likely lost)."""
    now = time.time()
    stale: list[str] = []
    for user in list(_logged_in_users):
        last = _session_activity.get(user)
        if last is None or (now - last) > miss_seconds:
            stale.append(user)
    return stale


# ─── FastAPI Dependencies ──────────────────────────────────────────


async def login_required(request: Request):
    """Redirect to /login if not authenticated or session timed out."""
    user = get_current_user(request)
    if not user:
        accept = request.headers.get("accept", "")
        if "text/html" not in accept:
            raise HTTPException(status_code=401, detail="Not authenticated")
        return RedirectResponse(url="/login", status_code=303)

    # Check timeout
    if check_timeout(user):
        request.session.pop("user", None)
        unregister_gpio_session(user)
        log_activity(user, "TIMEOUT_LOGOUT")
        from backend.gpio import manager as gpio_manager
        gpio_manager.on_session_logout(user)
        accept = request.headers.get("accept", "")
        if "text/html" not in accept:
            raise HTTPException(status_code=401, detail="Session expired")
        return RedirectResponse(url="/login", status_code=303)

    # Touch activity on valid request
    touch_activity(user)
    return user


async def admin_required(request: Request):
    """Raise 401 if not authenticated, 403 if not admin."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    if check_timeout(user):
        request.session.pop("user", None)
        unregister_gpio_session(user)
        log_activity(user, "TIMEOUT_LOGOUT")
        from backend.gpio import manager as gpio_manager
        gpio_manager.on_session_logout(user)
        raise HTTPException(status_code=401, detail="Session expired")

    if not USERS.get(user, {}).get("admin", False):
        raise HTTPException(status_code=403, detail="Admin access required")

    touch_activity(user)
    return user


# ─── Init ──────────────────────────────────────────────────────────

load_users()
_init_activity_log()
