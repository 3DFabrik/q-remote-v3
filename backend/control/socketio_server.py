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
            # Emit RSSI from display text (parsed from radio LCD)
            if lcd.rssi != -120:
                s_points = [
                    (-140, "S0"), (-121, "S1"), (-115, "S2"), (-109, "S3"), (-103, "S4"),
                    (-97, "S5"), (-91, "S6"), (-85, "S7"), (-79, "S8"), (-73, "S9"),
                ]
                s_unit = "S9+"
                for threshold, label in s_points:
                    if lcd.rssi <= threshold:
                        s_unit = label
                        break
                await sio.emit('rssi', {'dbm': lcd.rssi, 's_unit': s_unit})

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




async def _on_register(reg, val):
    """Register value from radio - BK4819 RSSI via register 0x67."""
    global _smoothed_dbm
    if reg == 0x67:
        rssi_raw = val & 0x1FF
        dbm = round((rssi_raw * 0.483) - 179)
        # Quansheng UV-K5 calibrated by Patric
        if rssi_raw <= 122: s_raw = 0           # S0 noise floor
        elif rssi_raw < 170:                     # S1-S9: 48 steps / 9 = ~5.3 per S
            s_raw = min(9, 1 + int((rssi_raw - 122) / 5.3))
        elif rssi_raw < 191: s_raw = 10          # S9+10dB
        elif rssi_raw < 212: s_raw = 11          # S9+20dB
        elif rssi_raw < 233: s_raw = 12          # S9+30dB
        elif rssi_raw < 254: s_raw = 13          # S9+40dB
        elif rssi_raw < 275: s_raw = 14          # S9+50dB
        else: s_raw = 15                         # S9+60dB
        # Attack/Release smoothing on S-unit scale
        _smoothed_s = getattr(_on_register, '_smoothed_s', 0.0)
        if s_raw > _smoothed_s:
            _smoothed_s = _smoothed_s + (s_raw - _smoothed_s) * 0.7
        else:
            _smoothed_s = _smoothed_s + (s_raw - _smoothed_s) * 0.15
        _on_register._smoothed_s = _smoothed_s
        s_out = round(_smoothed_s)
        # Format S-unit for display
        if s_out <= 0:
            s_unit = "S0"
        elif s_out <= 9:
            s_unit = f"S{s_out}"
        else:
            s_unit = f"S9+{(s_out - 9) * 10}dB"
        logger.info(f"RSSI raw={rssi_raw} dbm={dbm} s_out={s_out} s_unit={s_unit}")
        # Send both dbm (for logging) and s_out (0-15 for needle)
        await sio.emit("rssi", {"dbm": dbm, "s_unit": s_unit, "s_raw": s_out})


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


@sio.event
async def ptt_off(sid, data=None):
    global _ptt_owner
    if _ptt_owner != sid:
        return
    if radio:
        radio.send_key(13)
    _ptt_owner = None
    await sio.emit('ptt_status', {'active': False, 'holder': None})


@sio.event
async def request_rssi(sid, data=None):
    if radio and radio.connected:
        logger.info("RSSI requested")
        radio.request_rssi()
