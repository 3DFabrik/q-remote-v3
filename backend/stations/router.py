"""FastAPI router for Stations Editor endpoints."""

import asyncio
import csv
import io
import logging
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request, Depends, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, FileResponse

from backend.auth import login_required, get_current_user
from backend.stations.eeprom import (
    parse_eeprom, pack_channels, validate_channel,
    get_read_regions, get_write_regions, CTCSS_TONES,
    NUM_CHANNELS, DATA_SIZE, NAMES_SIZE, ATTR_SIZE,
)

logger = logging.getLogger(__name__)

stations_router = APIRouter(prefix="/stations", tags=["stations"])

# ─── State ────────────────────────────────────────────────────────

BACKUPS_DIR = Path(__file__).parent.parent.parent / "backups"
BACKUPS_DIR.mkdir(exist_ok=True)

# Cached EEPROM data (populated after read from radio)
_cached_channels: Optional[list[dict]] = None

# Background tasks
_tasks: dict[str, dict] = {}  # task_id -> {status, progress, total, result, error}


def _get_channels() -> list[dict]:
    """Return cached channels or empty list."""
    return _cached_channels or []


def _set_channels(channels: list[dict]):
    """Update cached channels."""
    global _cached_channels
    _cached_channels = channels


# ─── EEPROM Serial I/O (via RadioConnection) ─────────────────────

async def _eeprom_read_task(task_id: str):
    """Read EEPROM from radio in background."""
    import backend.control.socketio_server as _sio_mod; radio = _sio_mod.radio

    if not radio or not radio.connected:
        _tasks[task_id]["status"] = "error"
        _tasks[task_id]["error"] = "Radio not connected"
        return

    regions = get_read_regions()
    total_chunks = len(regions)
    _tasks[task_id]["total"] = total_chunks

    try:
        # Switch to EEPROM mode (pauses Remote UI)
        radio.enter_eeprom_mode()

        data_buf = bytearray(DATA_SIZE)
        attr_buf = bytearray(ATTR_SIZE)
        name_buf = bytearray(NAMES_SIZE)

        for idx, (offset, length) in enumerate(regions):
            if _tasks[task_id]["status"] == "cancelled":
                return

            response = radio.eeprom_read_chunk(offset, length, timeout=3.0)

            if response is None:
                _tasks[task_id]["status"] = "error"
                _tasks[task_id]["error"] = f"No response for offset 0x{offset:04X}"
                return

            # Route response to correct buffer
            if offset < 0x0D60:  # Data region
                data_buf[offset:offset + length] = response[:length]
            elif offset < 0x0F50:  # Attr region
                start = offset - 0x0D60
                attr_buf[start:start + length] = response[:length]
            else:  # Names region
                start = offset - 0x0F50
                name_buf[start:start + length] = response[:length]

            _tasks[task_id]["progress"] = idx + 1
            await asyncio.sleep(0.02)

    finally:
        # Always restore Remote UI mode
        radio.exit_eeprom_mode()

    # Parse all regions
    import hashlib
    channels = parse_eeprom(bytes(data_buf), bytes(name_buf), bytes(attr_buf))
    
    # Debug: dump first 8 channels raw data
    logger.info(f"EEPROM read complete: data={len(data_buf)} attr={len(attr_buf)} names={len(name_buf)}")
    for i in range(8):
        d = data_buf[i*16:(i+1)*16]
        n = name_buf[i*16:(i+1)*16]
        a = attr_buf[i] if i < len(attr_buf) else -1
        c = channels[i] if i < len(channels) else {}
        logger.info(f"  Ch{i}: data={bytes(d).hex()} name={bytes(n).hex()} attr={a} inUse={c.get("inUse")} freq={c.get("rxFreq")} name_str={c.get("name")}")
    
    _set_channels(channels)

    _tasks[task_id]["status"] = "completed"
    _tasks[task_id]["result"] = {"channels": len([c for c in channels if c["inUse"]])}


async def _eeprom_write_task(task_id: str, data: bytes, names: bytes, attrs: bytes):
    """Write EEPROM to radio in background."""
    import backend.control.socketio_server as _sio_mod; radio = _sio_mod.radio

    if not radio or not radio.connected:
        _tasks[task_id]["status"] = "error"
        _tasks[task_id]["error"] = "Radio not connected"
        return

    # Create backup before writing
    backup_id = _save_backup(data, names, attrs)

    regions = get_write_regions(data, names, attrs)
    total_chunks = len(regions)
    _tasks[task_id]["total"] = total_chunks

    try:
        radio.enter_eeprom_mode()

        for idx, (offset, chunk) in enumerate(regions):
            if _tasks[task_id]["status"] == "cancelled":
                return

            success = radio.eeprom_write_chunk(offset, chunk, timeout=3.0)
            if not success:
                _tasks[task_id]["status"] = "error"
                _tasks[task_id]["error"] = f"Write failed at offset 0x{offset:04X}"
                return

            _tasks[task_id]["progress"] = idx + 1
            await asyncio.sleep(0.05)

    except Exception as e:
        logger.error(f"EEPROM write error: {e}")
        _tasks[task_id]["status"] = "error"
        _tasks[task_id]["error"] = str(e)
        return
    finally:
        radio.exit_eeprom_mode()

    _tasks[task_id]["status"] = "completed"
    _tasks[task_id]["result"] = {"backup_id": backup_id}


# ─── Backup helpers ───────────────────────────────────────────────

def _save_backup(data: bytes, names: bytes, attrs: bytes) -> str:
    """Save a .chan backup file. Returns backup ID."""
    backup_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    backup_path = BACKUPS_DIR / f"{backup_id}.chan"
    backup_path.write_bytes(data + names + attrs)
    logger.info(f"Backup saved: {backup_path} ({backup_path.stat().st_size} bytes)")
    return backup_id


def _list_backups() -> list[dict]:
    """List available backups."""
    backups = []
    for f in sorted(BACKUPS_DIR.glob("*.chan"), reverse=True):
        if f.stat().st_size >= DATA_SIZE + NAMES_SIZE + ATTR_SIZE:
            ts_parts = f.stem.split("_")
            try:
                dt = datetime.strptime(f"{ts_parts[0]}_{ts_parts[1]}", "%Y%m%d_%H%M%S")
            except (ValueError, IndexError):
                dt = datetime.fromtimestamp(f.stat().st_mtime)
            backups.append({
                "id": f.stem,
                "timestamp": dt.isoformat(),
                "size": f.stat().st_size,
            })
    return backups


def _load_backup(backup_id: str) -> Optional[tuple[bytes, bytes, bytes]]:
    """Load a backup file. Returns (data, names, attrs) or None."""
    # Sanitize backup_id to prevent path traversal
    safe_id = backup_id.replace("/", "").replace("\\", "").replace("..", "")
    path = BACKUPS_DIR / f"{safe_id}.chan"
    if not path.exists():
        return None
    raw = path.read_bytes()
    if len(raw) < DATA_SIZE + NAMES_SIZE + ATTR_SIZE:
        return None
    return raw[:DATA_SIZE], raw[DATA_SIZE:DATA_SIZE + NAMES_SIZE], raw[DATA_SIZE + NAMES_SIZE:]


# ─── Routes ───────────────────────────────────────────────────────

@stations_router.get("", response_class=HTMLResponse)
async def stations_page(request: Request, _=Depends(login_required)):
    """Serve the stations editor HTML page."""
    from pathlib import Path
    html_path = Path(__file__).parent.parent.parent / "frontend" / "templates" / "stations.html"
    return HTMLResponse(content=html_path.read_text())


@stations_router.get("/api/stations")
async def api_get_stations(_=Depends(login_required)):
    """Return current channel list as JSON."""
    channels = _get_channels()
    return {"channels": channels, "total": len(channels), "used": len([c for c in channels if c["inUse"]])}


@stations_router.post("/api/stations/read")
async def api_stations_read(_=Depends(login_required)):
    """Trigger EEPROM read from radio. Returns task ID for polling."""
    task_id = uuid.uuid4().hex[:12]
    _tasks[task_id] = {
        "status": "running",
        "progress": 0,
        "total": 0,
        "result": None,
        "error": None,
        "started": time.time(),
    }
    asyncio.create_task(_eeprom_read_task(task_id))
    return {"task_id": task_id}


@stations_router.get("/api/stations/read/{task_id}/status")
async def api_read_status(task_id: str, _=Depends(login_required)):
    """Poll progress of EEPROM read."""
    task = _tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@stations_router.post("/api/stations/write")
async def api_stations_write(request: Request, _=Depends(login_required)):
    """Write channels back to radio EEPROM."""
    channels = _get_channels()
    if not channels:
        raise HTTPException(status_code=400, detail="No channels loaded. Read from radio first.")

    data, names, attrs = pack_channels(channels)

    task_id = uuid.uuid4().hex[:12]
    _tasks[task_id] = {
        "status": "running",
        "progress": 0,
        "total": 0,
        "result": None,
        "error": None,
        "started": time.time(),
    }
    asyncio.create_task(_eeprom_write_task(task_id, data, names, attrs))

    user = get_current_user(request)
    logger.info(f"EEPROM write initiated by {user}, task={task_id}")
    return {"task_id": task_id}


@stations_router.get("/api/stations/write/{task_id}/status")
async def api_write_status(task_id: str, _=Depends(login_required)):
    """Poll progress of EEPROM write."""
    task = _tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@stations_router.get("/api/stations/export/csv")
async def api_export_csv(_=Depends(login_required)):
    """Export current channels as CSV download."""
    channels = _get_channels()
    if not channels:
        raise HTTPException(status_code=400, detail="No channels to export")

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Channel", "Name", "RX Freq (MHz)", "TX Offset (MHz)", "Offset Dir",
        "RX Code", "TX Code", "RX Code Type", "TX Code Type",
        "Modulation", "Bandwidth", "Power", "Step",
        "Busy Lock", "Reverse", "PTT ID", "DTMF", "Scramble",
        "Compander", "Scanlist", "Band", "In Use",
    ])

    for ch in channels:
        if ch["inUse"]:
            writer.writerow([
                ch["number"], ch["name"],
                f"{ch['rxFreq']:.5f}", f"{ch['txOffset']:.5f}",
                ch["offsetDir"], ch["rxCode"], ch["txCode"],
                ch["rxCodeType"], ch["txCodeType"],
                ch["modulation"], ch["bandwidth"], ch["power"], ch["step"],
                ch["busyLock"], ch["reverse"], ch["pttId"], ch["dtmf"],
                ch["scramble"], ch["compander"], ch["scanlist"],
                ch["band"], ch["inUse"],
            ])

    output.seek(0)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=channels_{timestamp}.csv"},
    )


@stations_router.post("/api/stations/import/csv")
async def api_import_csv(file: UploadFile = File(...), _=Depends(login_required)):
    """Import CSV file, validate, return parsed channels."""
    content = await file.read()
    text = content.decode("utf-8", errors="replace")

    reader = csv.DictReader(io.StringIO(text))
    imported = []
    errors = []

    for row_idx, row in enumerate(reader):
        try:
            ch_num = int(row.get("Channel", row_idx + 1))
            ch = {
                "number": ch_num,
                "name": row.get("Name", ""),
                "rxFreq": float(row.get("RX Freq (MHz)", 0)),
                "txOffset": float(row.get("TX Offset (MHz)", 0)),
                "offsetDir": row.get("Offset Dir", "Off"),
                "rxCode": int(row.get("RX Code", 0)),
                "txCode": int(row.get("TX Code", 0)),
                "rxCodeType": row.get("RX Code Type", "None"),
                "txCodeType": row.get("TX Code Type", "None"),
                "modulation": row.get("Modulation", "FM"),
                "bandwidth": row.get("Bandwidth", "Wide"),
                "power": row.get("Power", "High"),
                "step": row.get("Step", "12.5kHz"),
                "busyLock": row.get("Busy Lock", "false").lower() in ("true", "1", "yes"),
                "reverse": row.get("Reverse", "false").lower() in ("true", "1", "yes"),
                "pttId": row.get("PTT ID", "Off"),
                "dtmf": row.get("DTMF", "false").lower() in ("true", "1", "yes"),
                "scramble": row.get("Scramble", "Off"),
                "compander": row.get("Compander", "Off"),
                "scanlist": row.get("Scanlist", "None"),
                "band": int(row.get("Band", 1)),
                "inUse": True,
            }

            ch_errors = validate_channel(ch)
            if ch_errors:
                errors.extend([f"Row {row_idx + 1}: {e}" for e in ch_errors])
            else:
                imported.append(ch)

        except (ValueError, KeyError) as e:
            errors.append(f"Row {row_idx + 1}: Parse error: {e}")

    return {"imported": len(imported), "errors": errors, "channels": imported}


@stations_router.get("/api/stations/backups")
async def api_list_backups(_=Depends(login_required)):
    """List available backups."""
    return {"backups": _list_backups()}


@stations_router.get("/api/stations/backups/{backup_id}")
async def api_download_backup(backup_id: str, _=Depends(login_required)):
    """Download a specific backup file."""
    safe_id = backup_id.replace("/", "").replace("\\", "").replace("..", "")
    path = BACKUPS_DIR / f"{safe_id}.chan"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Backup not found")
    return FileResponse(path, filename=f"{safe_id}.chan", media_type="application/octet-stream")


@stations_router.post("/api/stations/restore/{backup_id}")
async def api_restore_backup(backup_id: str, _=Depends(login_required)):
    """Restore channels from a backup."""
    result = _load_backup(backup_id)
    if not result:
        raise HTTPException(status_code=404, detail="Backup not found or invalid")

    data, names, attrs = result
    channels = parse_eeprom(data, names, attrs)
    _set_channels(channels)

    return {
        "restored": len([c for c in channels if c["inUse"]]),
        "total": len(channels),
    }


@stations_router.post("/api/stations/update")
async def api_update_channel(request: Request, _=Depends(login_required)):
    """Update a single channel in the cache."""
    body = await request.json()
    ch_num = body.get("number")
    if not ch_num or ch_num < 1 or ch_num > 200:
        raise HTTPException(status_code=400, detail="Invalid channel number")

    channels = _get_channels()
    if not channels:
        raise HTTPException(status_code=400, detail="No channels loaded")

    ch_errors = validate_channel(body)
    if ch_errors:
        raise HTTPException(status_code=400, detail=", ".join(ch_errors))

    channels[ch_num - 1] = body
    _set_channels(channels)
    return {"ok": True}
