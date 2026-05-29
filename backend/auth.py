"""Q-Remote V3 – Authentication module (session-based, Starlette sessions)."""

import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

from fastapi import Request, Depends, HTTPException
from starlette.responses import RedirectResponse

# ─── Config ────────────────────────────────────────────────────────

SECRET_KEY = "q-remote-v3-secret-change-me"
USER_FILE = Path(__file__).parent.parent / "users.json"
LOG_DIR = Path(__file__).parent.parent / "logs"

USERS: dict = {}

# ─── Activity Logging ──────────────────────────────────────────────

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


def save_users():
    with open(USER_FILE, "w") as f:
        json.dump(USERS, f, indent=4)


def get_current_user(request: Request) -> Optional[str]:
    """Get the currently logged-in username from the session, or None."""
    return request.session.get("user")


def is_admin(request: Request) -> bool:
    """Check if the current user is an admin."""
    user = get_current_user(request)
    return bool(user and USERS.get(user, {}).get("admin", False))


# ─── FastAPI Dependencies ──────────────────────────────────────────


async def login_required(request: Request):
    """Redirect to /login if not authenticated."""
    user = get_current_user(request)
    if not user:
        # For API/WebSocket requests, return 401
        accept = request.headers.get("accept", "")
        if "text/html" not in accept:
            raise HTTPException(status_code=401, detail="Not authenticated")
        return RedirectResponse(url="/login", status_code=303)
    return user


async def admin_required(request: Request):
    """Redirect to / if not admin, to /login if not authenticated."""
    user = get_current_user(request)
    if not user:
        accept = request.headers.get("accept", "")
        if "text/html" not in accept:
            raise HTTPException(status_code=401, detail="Not authenticated")
        return RedirectResponse(url="/login", status_code=303)
    if not USERS.get(user, {}).get("admin", False):
        return RedirectResponse(url="/", status_code=303)
    return user


# ─── Init ──────────────────────────────────────────────────────────

load_users()
_init_activity_log()
