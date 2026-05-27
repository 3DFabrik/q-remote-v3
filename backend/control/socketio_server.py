"""SocketIO server for real-time control communication.

Handles:
- Display updates (LCD rendering data broadcast to all clients)
- Button/key presses from clients to radio
- PTT control
- RSSI/S-Meter updates
- Connection status changes
- Client management (who's connected, PTT lock)
"""

import asyncio
import logging
from typing import Optional

import socketio

from backend.radio.adapter import RadioState

logger = logging.getLogger(__name__)

# ─── SocketIO Server (async mode) ─────────────────────────────────

sio = socketio.AsyncServer(
    async_mode='asgi',
    cors_allowed_origins='*',
    ping_interval=25,
    ping_timeout=10,
)

# Shared state
_radio = None          # Will be set by app.py
_ptt_owner: Optional[str] = None   # Session ID of current PTT holder


def set_radio(radio):
    """Set the radio adapter reference (called from app.py)."""
    global _radio
    _radio = radio
    
    # Wire up radio callbacks
    radio.on_display_update = _on_display_update
    radio.on_rssi_update = _on_rssi_update
    radio.on_state_change = _on_state_change


def get_sio_app():
    """Get the ASGI application for mounting in FastAPI."""
    return socketio.ASGIApp(sio)


# ─── Radio Callbacks → SocketIO Broadcasts ────────────────────────

async def _on_display_update(data: bytes):
    """Radio sent display data → broadcast to all clients."""
    await sio.emit('display', {'data': list(data)})


async def _on_rssi_update(dbm: float, s_unit: str):
    """Radio sent RSSI update → broadcast to all clients."""
    await sio.emit('rssi', {'dbm': round(dbm, 1), 's_unit': s_unit})


async def _on_state_change(state: RadioState):
    """Radio connection state changed → broadcast to all clients."""
    await sio.emit('radio_state', {'state': state.value})


# ─── Connection Events ────────────────────────────────────────────

@sio.event
async def connect(sid, environ):
    """Client connected."""
    logger.info(f"Client connected: {sid}")
    
    # Send current radio state
    if _radio:
        info = await _radio.get_info()
        await sio.emit('radio_state', {'state': info.state.value}, to=sid)


@sio.event
async def disconnect(sid):
    """Client disconnected."""
    global _ptt_owner
    logger.info(f"Client disconnected: {sid}")
    
    # Release PTT if this client was holding it
    if _ptt_owner == sid:
        if _radio:
            await _radio.set_ptt(False)
        _ptt_owner = None
        await sio.emit('ptt_status', {'active': False, 'holder': None})


# ─── Control Events (Client → Server → Radio) ────────────────────

@sio.event
async def key_press(sid, data):
    """Client pressed a key on the radio.
    
    Expected data: {'keycode': int}
    """
    keycode = data.get('keycode')
    if keycode is None or keycode < 0 or keycode > 19:
        logger.warning(f"Invalid keycode from {sid}: {keycode}")
        return
    
    if not _radio or _radio.state != RadioState.CONNECTED:
        return
    
    await _radio.send_key(keycode)
    logger.debug(f"Key press from {sid}: {keycode}")


@sio.event
async def ptt_on(sid, data=None):
    """Client requests PTT on (start transmitting).
    
    Only one client can hold PTT at a time.
    """
    global _ptt_owner
    
    if not _radio or _radio.state != RadioState.CONNECTED:
        await sio.emit('ptt_status', {'active': False, 'error': 'Radio not connected'}, to=sid)
        return
    
    if _ptt_owner is not None and _ptt_owner != sid:
        await sio.emit('ptt_status', {'active': False, 'error': 'PTT locked by another user'}, to=sid)
        return
    
    _ptt_owner = sid
    await _radio.set_ptt(True)
    await sio.emit('ptt_status', {'active': True, 'holder': sid})
    logger.info(f"PTT ON from {sid}")


@sio.event
async def ptt_off(sid, data=None):
    """Client releases PTT (stop transmitting)."""
    global _ptt_owner
    
    if _ptt_owner != sid:
        return  # Can't release what you don't hold
    
    if _radio:
        await _radio.set_ptt(False)
    
    _ptt_owner = None
    await sio.emit('ptt_status', {'active': False, 'holder': None})
    logger.info(f"PTT OFF from {sid}")


@sio.event
async def request_rssi(sid, data=None):
    """Client requests an RSSI reading."""
    if _radio and _radio.state == RadioState.CONNECTED:
        await _radio.get_rssi()


@sio.event
async def request_display(sid, data=None):
    """Client requests a screen dump."""
    if _radio and _radio.state == RadioState.CONNECTED:
        await _radio.request_display()
