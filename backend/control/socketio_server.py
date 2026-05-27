"""SocketIO server for real-time control communication.
Adapted to work with V1's RadioConnection.
"""

import asyncio
import logging
from typing import Optional

import socketio

from backend.radio.connection import RadioConnection
from backend.radio.protocol import Packet

logger = logging.getLogger(__name__)

sio = socketio.AsyncServer(
    async_mode='asgi',
    cors_allowed_origins='*',
    ping_interval=25,
    ping_timeout=10,
)

radio: Optional[RadioConnection] = None
_ptt_owner: Optional[str] = None


def init_radio():
    """Create and configure the radio connection."""
    global radio
    radio = RadioConnection()

    # Wire up callbacks
    radio.on_ui = _on_ui
    radio.on_rssi = _on_rssi
    radio.on_connect = _on_radio_connect
    radio.on_disconnect = _on_radio_disconnect

    return radio


def get_sio_app():
    return socketio.ASGIApp(sio, socketio_path='socket.io')


# ─── Radio Callbacks ─────────────────────────────────────────────

async def _on_ui(ui_type, val1, val2, val3, data_len, data):
    """Radio sent UI data → broadcast to all clients."""
    await sio.emit('display', {
        'type': ui_type,
        'val1': val1,
        'val2': val2,
        'val3': val3,
        'dataLen': data_len,
        'data': list(data) if data else [],
    })


async def _on_rssi(raw_data):
    """Radio sent RSSI → parse and broadcast."""
    if len(raw_data) >= 4:
        rssi_raw = raw_data[2] | (raw_data[3] << 8)
        dbm = -(rssi_raw & 0x3FF) / 2.0

        s_points = [
            (-121, "S1"), (-115, "S2"), (-109, "S3"), (-103, "S4"),
            (-97, "S5"), (-91, "S6"), (-85, "S7"), (-79, "S8"), (-73, "S9"),
        ]
        s_unit = "S9+"
        for threshold, label in s_points:
            if dbm <= threshold:
                s_unit = label
                break

        await sio.emit('rssi', {'dbm': round(dbm, 1), 's_unit': s_unit})


async def _on_radio_connect():
    await sio.emit('radio_state', {'state': 'connected'})


async def _on_radio_disconnect():
    global _ptt_owner
    _ptt_owner = None
    await sio.emit('radio_state', {'state': 'disconnected'})


# ─── SocketIO Events ─────────────────────────────────────────────

@sio.event
async def connect(sid, environ):
    logger.info(f"Client connected: {sid}")
    if radio and radio.connected:
        await sio.emit('radio_state', {'state': 'connected'}, to=sid)
    else:
        await sio.emit('radio_state', {'state': 'disconnected'}, to=sid)


@sio.event
async def disconnect(sid):
    global _ptt_owner
    logger.info(f"Client disconnected: {sid}")
    if _ptt_owner == sid:
        if radio:
            radio.send_key(13)  # Release PTT (EXIT key)
        _ptt_owner = None
        await sio.emit('ptt_status', {'active': False, 'holder': None})


@sio.event
async def key_press(sid, data):
    keycode = data.get('keycode', 0)
    if radio and radio.connected:
        radio.send_key(keycode)


@sio.event
async def ptt_on(sid, data=None):
    global _ptt_owner
    if not radio or not radio.connected:
        return
    if _ptt_owner is not None and _ptt_owner != sid:
        await sio.emit('ptt_status', {'active': False, 'error': 'PTT locked'}, to=sid)
        return
    _ptt_owner = sid
    radio.send_key(16)  # V1 PTT key code
    await sio.emit('ptt_status', {'active': True, 'holder': sid})


@sio.event
async def ptt_off(sid, data=None):
    global _ptt_owner
    if _ptt_owner != sid:
        return
    if radio:
        radio.send_key(13)  # V1 EXIT key = PTT release
    _ptt_owner = None
    await sio.emit('ptt_status', {'active': False, 'holder': None})


@sio.event
async def request_rssi(sid, data=None):
    if radio and radio.connected:
        radio.request_rssi()


@sio.event
async def request_display(sid, data=None):
    if radio and radio.connected:
        radio.request_screen()
