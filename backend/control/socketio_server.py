"""SocketIO server for real-time control communication.
Uses V1's LCDDisplay to process type 5/6 packets into display fragments.
"""

import asyncio
import logging
from typing import Optional
from http.cookies import SimpleCookie
from starlette.middleware.sessions import SessionMiddleware

import socketio

from backend.radio.connection import RadioConnection
from backend.radio.protocol import Packet
from backend.radio.lcd import LCDDisplay
from backend.auth import USERS, SECRET_KEY

logger = logging.getLogger(__name__)

sio = socketio.AsyncServer(
    async_mode='asgi',
    cors_allowed_origins='*',
    ping_interval=25,
    ping_timeout=10,
)

radio: Optional[RadioConnection] = None
lcd: Optional[LCDDisplay] = None
_ptt_owner: Optional[str] = None

# RSSI smoothing state: running average of raw register values
_rssi_raw_history = []
_RSSI_HISTORY_LEN = 10


def _get_user_from_environ(environ: dict) -> Optional[str]:
    """Extract username from session cookie in SocketIO environ."""
    from starlette.requests import Request
    from http.cookies import SimpleCookie

    cookie_str = environ.get('HTTP_COOKIE', '')
    if not cookie_str:
        return None

    # Use starlette's SessionMiddleware to decode the session
    # We need to decode the signed cookie manually
    try:
        from starlette.middleware.sessions import SessionMiddleware
        import itsdangerous
        signer = itsdangerous.TimestampSigner(SECRET_KEY)
        cookie = SimpleCookie()
        cookie.load(cookie_str)
        session_cookie = cookie.get('session')
        if session_cookie:
            data = signer.unsign(session_cookie.value, max_age=None)
            import base64, json
            session_data = json.loads(base64.b64decode(data))
            return session_data.get('user')
    except Exception as e:
        logger.debug(f"Session decode failed: {e}")
    return None


def init_radio():
    global radio, lcd
    radio = RadioConnection()
    lcd = LCDDisplay()

    # LCD change callback -> broadcast lcd_update to clients
    def on_lcd_change(state):
        logger.info(f"LCD change callback fired!")
        if radio._loop:
            radio._loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(_emit_lcd(state))
            )
        else:
            logger.warning("LCD change: no event loop!")

    lcd.on_change(on_lcd_change)

    # Wire radio UI callback to LCD
    async def handle_ui(ui_type, val1, val2, val3, data_len, data):
        lcd.process_ui_packet(ui_type, val1, val2, val3, data_len, data)
        # Only flush after type 6 (status) – end of display frame
        if ui_type == 6:
            lcd.flush()
            # Check RSSI timeout – if no display text for 3s, reset
            lcd.check_rssi_timeout()
            # Emit RSSI (including s_raw=0 when timed out)
            if lcd.rssi == -120:
                await sio.emit('rssi', {'dbm': -120})
            else:
                # Send raw dBm – frontend does continuous S-unit mapping
                dbm = lcd.rssi
                # Determine s_unit label for text display
                if dbm <= -121: s_unit = "S0"
                elif dbm <= -115: s_unit = "S1"
                elif dbm <= -109: s_unit = "S2"
                elif dbm <= -103: s_unit = "S3"
                elif dbm <= -97: s_unit = "S4"
                elif dbm <= -91: s_unit = "S5"
                elif dbm <= -85: s_unit = "S6"
                elif dbm <= -79: s_unit = "S7"
                elif dbm <= -73: s_unit = "S8"
                elif dbm <= -63: s_unit = "S9"
                elif dbm <= -53: s_unit = "S9+20"
                elif dbm <= -43: s_unit = "S9+30"
                elif dbm <= -33: s_unit = "S9+40"
                elif dbm <= -23: s_unit = "S9+50"
                else: s_unit = "S9+60"
                await sio.emit('rssi', {'dbm': dbm, 's_unit': s_unit})

    radio.on_ui = handle_ui
    radio.on_rssi = _on_rssi
    radio.on_register = _on_register
    radio.on_connect = _on_radio_connect
    radio.on_disconnect = _on_radio_disconnect

    return radio


def get_sio_app():
    return socketio.ASGIApp(sio, socketio_path='socket.io')


# ─── Emit helpers ─────────────────────────────────────────────────

async def _emit_lcd(state):
    await sio.emit('lcd_update', state)


async def _on_rssi(raw_data):
    """RSSI from GET_RSSI - always returns garbage (-156), ignore."""
    pass


def _raw_to_s_raw(rssi_raw):
    """Convert raw BK4819 register value to S-unit (0-15)."""
    if rssi_raw <= 122: return 0
    elif rssi_raw < 170:
        return min(9, 1 + int((rssi_raw - 122) / 5.3))
    elif rssi_raw < 191: return 10
    elif rssi_raw < 212: return 11
    elif rssi_raw < 233: return 12
    elif rssi_raw < 254: return 13
    elif rssi_raw < 275: return 14
    else: return 15


async def _on_register(reg, val):
    """Register value from radio - BK4819 RSSI via register 0x67.
    Disabled: register values are unreliable, using display-text RSSI instead."""
    pass


async def _on_radio_connect():
    await sio.emit('radio_state', {'state': 'connected'})


async def _on_radio_disconnect():
    global _ptt_owner
    _ptt_owner = None
    await sio.emit('radio_state', {'state': 'disconnected'})


# ─── SocketIO Events ─────────────────────────────────────────────

@sio.event
async def connect(sid, environ):
    # Auth check: verify session cookie
    user = _get_user_from_environ(environ)
    if not user:
        logger.warning(f"SocketIO connect rejected (no session): {sid}")
        return False  # Reject connection

    logger.info(f"Client connected: {sid} (user={user})")
    # Store user on sid for later use
    await sio.save_session(sid, {'user': user})
    if radio and radio.connected:
        await sio.emit('radio_state', {'state': 'connected'}, to=sid)
        # Send current LCD state immediately
        if lcd:
            await sio.emit('lcd_update', lcd.get_state(), to=sid)
    else:
        await sio.emit('radio_state', {'state': 'disconnected'}, to=sid)


@sio.event
async def disconnect(sid):
    global _ptt_owner
    logger.info(f"Client disconnected: {sid}")
    if _ptt_owner == sid:
        if radio:
            radio.send_key(13)
        _ptt_owner = None
        await sio.emit('ptt_status', {'active': False, 'holder': None})


@sio.event
async def key_press(sid, data):
    keycode = data.get('keycode', 0)
    if radio and radio.connected:
        radio.send_key(keycode)
        # Force display refresh after radio has time to respond
        if lcd:
            await asyncio.sleep(0.15)
            lcd.force_flush()


@sio.event
async def ptt_on(sid, data=None):
    global _ptt_owner
    if not radio or not radio.connected:
        return
    if _ptt_owner is not None and _ptt_owner != sid:
        await sio.emit('ptt_status', {'active': False, 'error': 'PTT locked'}, to=sid)
        return
    _ptt_owner = sid
    radio.send_key(16)
    await sio.emit('ptt_status', {'active': True, 'holder': sid})
    # Force display refresh after radio has time to respond
    if lcd:
        await asyncio.sleep(0.15)
        lcd.force_flush()


@sio.event
async def ptt_off(sid, data=None):
    global _ptt_owner
    if _ptt_owner != sid:
        return
    if radio:
        radio.send_key(13)
    _ptt_owner = None
    await sio.emit('ptt_status', {'active': False, 'holder': None})
    # Force display refresh after radio has time to respond
    if lcd:
        await asyncio.sleep(0.15)
        lcd.force_flush()


@sio.event
async def request_rssi(sid, data=None):
    if radio and radio.connected:
        logger.info("RSSI requested")
        radio.request_rssi()
