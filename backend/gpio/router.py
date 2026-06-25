"""GPIO REST API routes for Q-Remote V3.

Admin-only endpoints for GPIO configuration and control.
All endpoints return JSON.

Spec: docs/SPEC-GPIO.md
"""

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request, Depends
from fastapi.responses import JSONResponse
import yaml

from backend.auth import admin_required, login_required
from backend.config import load_config, get, _PROJECT_ROOT
from backend.gpio.manager import manager, ALL_PINS, SYSTEM_RESERVED, TRIGGER_LABELS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/gpio", tags=["gpio"])

_LOCAL_CONFIG = _PROJECT_ROOT / "config.local.yaml"


# ─── Helpers ───────────────────────────────────────────────────────

def _save_gpio_config(pin_dicts: list[dict]):
    """Persist GPIO pin configuration to config.local.yaml."""
    config = {}
    if _LOCAL_CONFIG.exists():
        with open(_LOCAL_CONFIG, "r") as f:
            config = yaml.safe_load(f) or {}

    # Replace the entire gpio.pins section
    config["gpio"] = {"pins": pin_dicts}
    _LOCAL_CONFIG.write_text(yaml.dump(config, default_flow_style=False, sort_keys=False))

    # Invalidate config cache so get() returns fresh data
    load_config(force_reload=True)


# ─── Pin Discovery ─────────────────────────────────────────────────

@router.get("/pins")
async def get_pins(request: Request, _=Depends(admin_required)):
    """Return available BCM pins for GPIO assignment.

    Excludes system-reserved pins (I2C etc.).
    """
    configured = {c["bcm_pin"] for c in manager.get_configs()}
    pins = [
        {"bcm": p, "available": p not in configured, "reserved": p in SYSTEM_RESERVED}
        for p in ALL_PINS
    ]
    return {"pins": pins, "reserved": sorted(SYSTEM_RESERVED)}


@router.get("/triggers")
async def get_triggers(request: Request, _=Depends(admin_required)):
    """Return available trigger types with labels."""
    return {"triggers": [{"value": k, "label": v} for k, v in TRIGGER_LABELS.items()]}


# ─── Configuration ────────────────────────────────────────────────

@router.get("/config")
async def get_gpio_config(request: Request, _=Depends(admin_required)):
    """Return current GPIO pin configuration."""
    return {"pins": manager.get_configs()}


@router.post("/config")
async def save_gpio_config(request: Request, _=Depends(admin_required)):
    """Save full GPIO pin configuration.

    Replaces all existing GPIO config.
    Validates pin numbers and trigger values.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    pins = body.get("pins")
    if not isinstance(pins, list):
        return JSONResponse({"error": "'pins' must be a list"}, status_code=400)

    # Validate
    for i, pin_cfg in enumerate(pins):
        if not isinstance(pin_cfg, dict):
            return JSONResponse({"error": f"pins[{i}] must be an object"}, status_code=400)
        bcm = pin_cfg.get("bcm_pin")
        if not isinstance(bcm, int) or bcm not in ALL_PINS:
            return JSONResponse({"error": f"pins[{i}].bcm_pin invalid: {bcm}"}, status_code=400)
        if bcm in SYSTEM_RESERVED:
            return JSONResponse({"error": f"pins[{i}].bcm_pin {bcm} is system-reserved"}, status_code=400)

        # Check duplicate pins
        for j, other in enumerate(pins):
            if i != j and other.get("bcm_pin") == bcm:
                return JSONResponse({"error": f"Duplicate bcm_pin {bcm}"}, status_code=400)

    # Validate logic_level
    for pin_cfg in pins:
        if pin_cfg.get("logic_level") not in ("active_high", "active_low", None, ""):
            return JSONResponse(
                {"error": f"Invalid logic_level for pin {pin_cfg.get('bcm_pin')}"},
                status_code=400,
            )

    # Persist
    _save_gpio_config(pins)

    # Reload into manager
    manager.load_configs(pins)

    from backend.auth import log_activity, get_current_user
    log_activity(get_current_user(request), "GPIO_CONFIG", f"pins={len(pins)}")

    return {"status": "ok", "pins": len(pins)}


# ─── Live Status ──────────────────────────────────────────────────

@router.get("/status")
async def get_gpio_status(request: Request, _=Depends(admin_required)):
    """Return live status of all configured GPIO pins."""
    return {"pins": manager.get_status(), "initialized": manager.initialized}


# ─── Header Button Control ────────────────────────────────────────

@router.get("/buttons")
async def get_button_info(request: Request, _=Depends(login_required)):
    """Return header button labels and states for frontend top-bar."""
    return {"buttons": manager.get_button_info()}


@router.post("/button/{button_id}")
async def toggle_button(button_id: str, request: Request, _=Depends(login_required)):
    """Toggle a header button (button1 or button2).

    Body: {"active": true/false}
    """
    if button_id not in ("button1", "button2"):
        return JSONResponse({"error": f"Unknown button: {button_id}"}, status_code=400)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    active = body.get("active")
    if not isinstance(active, bool):
        return JSONResponse({"error": "'active' must be boolean"}, status_code=400)

    manager.on_button_toggle(button_id, active)

    from backend.auth import log_activity, get_current_user
    log_activity(get_current_user(request), "GPIO_BUTTON", f"{button_id}={'ON' if active else 'OFF'}")

    # Broadcast to all connected clients so button states stay in sync
    try:
        from backend.control.socketio_server import sio
        import asyncio
        asyncio.ensure_future(sio.emit("gpio_buttons", {"buttons": manager.get_button_info()}))
    except Exception:
        pass

    return {"status": "ok", "button": button_id, "active": active, "buttons": manager.get_button_info()}


# ─── Initialization ──────────────────────────────────────────────

@router.post("/reinit")
async def reinitialize(request: Request, _=Depends(admin_required)):
    """Re-initialize GPIO pins from config (all OFF first, then reconfigure).

    Useful after config changes without service restart.
    """
    await manager.initialize()
    return {"status": "ok", "pins": len(manager.get_status())}
