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
from backend.gpio.manager import (
    manager, ALL_PINS, SYSTEM_RESERVED, TRIGGER_LABELS,
    OUTPUT_PINS, ALL_RESERVED, HARD_RESERVED, SOFT_RESERVED,
    UART_PINS, DS18B20_PIN,
)

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


# ─── Pin Validation Helpers ────────────────────────────────────────

# Reverse lookup maps for _reserved_reason
_PIN_GROUPS = [
    ("I2C bus", SYSTEM_RESERVED),
    ("UART (TXD/RXD)", UART_PINS),
    ("1-Wire (DS18B20)", {DS18B20_PIN}),
    ("SPI0", {7, 8, 9, 10, 11}),
    ("Hardware PWM", {12, 13}),
    ("PCM/I2S", {18, 19, 20, 21}),
]


def _reserved_reason(bcm: int) -> str:
    """Return a human-readable reason why a pin is reserved, or '' if not reserved."""
    for label, group in _PIN_GROUPS:
        if bcm in group:
            return label
    return ''


# ─── Pin Discovery ─────────────────────────────────────────────────

@router.get("/pins")
async def get_pins(request: Request, _=Depends(admin_required)):
    """Return available BCM pins for GPIO assignment.

    Marks all reserved pins (I2C, UART, SPI, PWM, PCM, 1-Wire) as unavailable.
    """
    configured = {c["bcm_pin"] for c in manager.get_configs()}
    pins = []
    for p in ALL_PINS:
        is_reserved = p in ALL_RESERVED
        is_configured = p in configured
        pins.append({
            "bcm": p,
            "available": not is_reserved and not is_configured,
            "reserved": is_reserved,
            "reason": _reserved_reason(p) if is_reserved else "",
        })
    return {
        "pins": pins,
        "reserved": sorted(ALL_RESERVED),
        "hard_reserved": sorted(HARD_RESERVED),
        "soft_reserved": sorted(SOFT_RESERVED),
        "output_pins": OUTPUT_PINS,
        "ds18b20_pin": DS18B20_PIN,
    }


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

        direction = pin_cfg.get("direction", "output")
        input_type = pin_cfg.get("input_type", "")

        # Hard-reserved pins (I2C, UART) are never allowed
        if bcm in HARD_RESERVED:
            reason = _reserved_reason(bcm)
            return JSONResponse(
                {"error": f"pins[{i}].bcm_pin {bcm} is reserved ({reason})"},
                status_code=400,
            )

        # DS18B20 input: only BCM 4 is allowed
        if direction == "input" and input_type == "ds18b20":
            if bcm != DS18B20_PIN:
                return JSONResponse(
                    {"error": f"pins[{i}].bcm_pin {bcm}: DS18B20 requires BCM {DS18B20_PIN}"},
                    status_code=400,
                )
        elif direction == "output":
            # Output pins must not be in ALL_RESERVED
            if bcm in ALL_RESERVED:
                reason = _reserved_reason(bcm)
                return JSONResponse(
                    {"error": f"pins[{i}].bcm_pin {bcm} is reserved ({reason})"},
                    status_code=400,
                )

        # Check duplicate pins
        for j, other in enumerate(pins):
            if i != j and other.get("bcm_pin") == bcm:
                return JSONResponse({"error": f"Duplicate bcm_pin {bcm}"}, status_code=400)

    # Check duplicate button triggers (each header button can only be assigned once)
    seen_buttons = set()
    for pin_cfg in pins:
        trigger = pin_cfg.get("trigger", "")
        if trigger in ("button1", "button2"):
            if trigger in seen_buttons:
                return JSONResponse(
                    {"error": f"Trigger '{trigger}' is already assigned to another pin (each button can only be used once)"},
                    status_code=400,
                )
            seen_buttons.add(trigger)

    # Validate logic_level
    for pin_cfg in pins:
        if pin_cfg.get("logic_level") not in ("active_high", "active_low", None, ""):
            return JSONResponse(
                {"error": f"Invalid logic_level for pin {pin_cfg.get('bcm_pin')}"},
                status_code=400,
            )

    # Persist and apply to hardware (all pins OFF first, then reconfigure)
    _save_gpio_config(pins)
    await manager.initialize()

    from backend.auth import log_activity, get_current_user
    log_activity(get_current_user(request), "GPIO_CONFIG", f"pins={len(pins)}")

    return {
        "status": "ok",
        "pins": len(pins),
        "active": len(manager.get_status()),
        "initialized": manager.initialized,
    }


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

    await manager.on_button_toggle(button_id, active)

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


# ─── Temperature Sensors (DS18B20) ────────────────────────────────

@router.get("/temperatures")
async def get_temperatures(request: Request, _=Depends(login_required)):
    """Return current temperature readings from all configured DS18B20 sensors.

    Returns: {"sensors": [{name, id, temp, pin, error?}, ...]}
    Temperature is in °C with 1 decimal precision, or null if unreadable.
    """
    sensors = manager.get_temperatures()
    return {"sensors": sensors}


# ─── Sensor Detection Status ─────────────────────────────────────

@router.get("/sensor-status")
async def get_sensor_status(request: Request, _=Depends(admin_required)):
    """Return physical detection status for all configured DS18B20 sensors.

    Used by admin page to show warnings when a sensor is configured
    but not physically detected.
    """
    from pathlib import Path as _Path
    w1_base = _Path("/sys/bus/w1/devices")
    statuses = []

    for cfg in manager._configs:
        if cfg.input_type != "ds18b20":
            continue

        status = {
            "bcm_pin": cfg.bcm_pin,
            "sensor_name": cfg.sensor_name,
            "sensor_id": cfg.sensor_id,
            "detected": False,
            "error": "",
        }

        sensor_id = cfg.sensor_id
        if not sensor_id and w1_base.exists():
            for dev_dir in w1_base.iterdir():
                if dev_dir.name.startswith("28-"):
                    sensor_id = dev_dir.name
                    break

        if not sensor_id:
            status["error"] = "No sensor detected (no 28-* device in /sys/bus/w1/devices)"
        else:
            w1_file = w1_base / sensor_id / "w1_slave"
            if w1_file.exists():
                status["detected"] = True
            else:
                status["error"] = f"Sensor ID {sensor_id} not connected"

        statuses.append(status)

    return {"sensors": statuses}


# ─── Initialization ──────────────────────────────────────────────

@router.post("/reinit")
async def reinitialize(request: Request, _=Depends(admin_required)):
    """Re-initialize GPIO pins from config (all OFF first, then reconfigure).

    Useful after config changes without service restart.
    """
    await manager.initialize()
    return {"status": "ok", "pins": len(manager.get_status())}
