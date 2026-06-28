"""SocketIO server for real-time control communication.
Uses V1's LCDDisplay to process type 5/6 packets into display fragments.
"""

import asyncio
import time
import logging
from typing import Optional
from http.cookies import SimpleCookie
from starlette.middleware.sessions import SessionMiddleware

import socketio

from backend.radio.connection import RadioConnection
from backend.radio.protocol import Packet
from backend.radio.lcd import LCDDisplay
from backend.auth import USERS, SECRET_KEY, log_activity, touch_activity, check_timeout, clear_activity, get_valid_user_from_cookie_data
from backend.gpio import manager as gpio_manager

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
_sid_users: dict = {}  # sid -> username
_ptt_drain_task: Optional[asyncio.Task] = None
_tone_1750_busy = False
_PTT_DRAIN_DELAY = 0.8

_rssi_raw_history = []
_RSSI_HISTORY_LEN = 10


def _feed_rx_squelch_from_lcd() -> None:
    """Push current LCD RSSI into the RX noise gate (same logic as S-meter)."""
    if not lcd:
        return
    lcd.check_rssi_timeout()
    try:
        from backend.app import rx_audio
        rx_audio.update_signal_dbm(lcd.rssi)
    except Exception as exc:
        logger.debug("RX squelch RSSI feed failed: %s", exc)


def _get_user_from_environ(environ: dict) -> Optional[str]:
    """Extract username from session cookie in SocketIO environ."""
    try:
        from starlette.middleware.sessions import SessionMiddleware
        import itsdangerous
        signer = itsdangerous.TimestampSigner(SECRET_KEY)
        cookie = SimpleCookie()
        cookie.load(environ.get('HTTP_COOKIE', ''))
        session_cookie = cookie.get('session')
        if session_cookie:
            data = signer.unsign(session_cookie.value, max_age=None)
            import base64, json
            session_data = json.loads(base64.b64decode(data))
            return get_valid_user_from_cookie_data(session_data)
    except Exception as e:
        logger.debug(f"Session decode failed: {e}")
    return None


def init_radio():
    global radio, lcd
    radio = RadioConnection()
    lcd = LCDDisplay()

    def on_lcd_change(state):
        logger.info(f"LCD change callback fired!")
        if radio._loop:
            radio._loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(_emit_lcd(state))
            )
        else:
            logger.warning("LCD change: no event loop!")

    lcd.on_change(on_lcd_change)

    async def handle_ui(ui_type, val1, val2, val3, data_len, data):
        lcd.process_ui_packet(ui_type, val1, val2, val3, data_len, data)
        _feed_rx_squelch_from_lcd()
        if ui_type == 6:
            lcd.flush()
            lcd.check_rssi_timeout()
            _feed_rx_squelch_from_lcd()
            if lcd.rssi == -120:
                await sio.emit('rssi', {'dbm': -120})
            else:
                dbm = lcd.rssi
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
    pass


def _raw_to_s_raw(rssi_raw):
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
    pass


async def _on_radio_connect():
    await sio.emit('radio_state', {'state': 'connected'})


async def _on_radio_disconnect():
    global _ptt_owner
    _ptt_owner = None
    if radio:
        radio.force_release_ptt()
    asyncio.create_task(gpio_manager.on_ptt(False))
    await sio.emit('radio_state', {'state': 'disconnected'})


# ─── SocketIO Events ─────────────────────────────────────────────

@sio.event
async def connect(sid, environ):
    user = _get_user_from_environ(environ)
    if not user:
        logger.warning(f"SocketIO connect rejected (no session): {sid}")
        return False

    # Check timeout
    if check_timeout(user):
        logger.warning(f"SocketIO connect rejected (session expired): {sid} user={user}")
        return False

    logger.info(f"Client connected: {sid} (user={user})")
    await sio.save_session(sid, {'user': user})
    _sid_users[sid] = user
    touch_activity(user)

    if radio and radio.connected:
        await sio.emit('radio_state', {'state': 'connected'}, to=sid)
        if lcd:
            await sio.emit('lcd_update', lcd.get_state(), to=sid)
    else:
        await sio.emit('radio_state', {'state': 'disconnected'}, to=sid)


@sio.event
async def disconnect(sid):
    global _ptt_owner
    logger.info(f"Client disconnected: {sid}")
    user = _sid_users.pop(sid, None)
    if _ptt_owner == sid:
        if radio:
            radio.send_key(13)
        _ptt_owner = None
        await sio.emit('ptt_status', {'active': False, 'holder': None, 'user': None})


@sio.event
async def key_press(sid, data):
    user = _sid_users.get(sid)
    if not user:
        return
    # Check timeout on activity
    if check_timeout(user):
        await sio.disconnect(sid)
        return
    touch_activity(user)

    keycode = data.get('keycode', 0)
    if radio and radio.connected:
        radio.send_key(keycode)
        if lcd:
            await asyncio.sleep(0.15)
            lcd.force_flush()


@sio.event
async def ptt_on(sid, data=None):
    global _ptt_owner, _ptt_drain_task
    user = _sid_users.get(sid)
    if not user:
        return
    if check_timeout(user):
        await sio.disconnect(sid)
        return
    touch_activity(user)

    if not radio or not radio.connected:
        return
    if _ptt_drain_task and not _ptt_drain_task.done():
        _ptt_drain_task.cancel()
        _ptt_drain_task = None
    if _ptt_owner is not None and _ptt_owner != sid:
        await sio.emit('ptt_status', {'active': False, 'error': 'PTT locked'}, to=sid)
        return
    _ptt_owner = sid
    radio.send_key(16)
    await asyncio.sleep(0.1)
    freq = await radio.read_frequency() or 'N/A'
    log_activity(user, 'PTT_ON', f'freq={freq}')
    await gpio_manager.on_ptt(True)
    await sio.emit('ptt_status', {'active': True, 'holder': sid, 'user': user})
    if lcd:
        await asyncio.sleep(0.15)
        lcd.force_flush()


@sio.event
async def tone_1750(sid, data=None):
    """Send 1750 Hz repeater tone. Auto-engages PTT if idle; releases after tone unless user holds PTT."""
    global _tone_1750_busy, _ptt_owner, _ptt_drain_task
    user = _sid_users.get(sid)
    if not user:
        return
    if check_timeout(user):
        await sio.disconnect(sid)
        return
    touch_activity(user)

    if not radio or not radio.connected:
        await sio.emit('tone_1750_status', {'ok': False, 'error': 'Radio offline'}, to=sid)
        return
    if _tone_1750_busy:
        return
    if _ptt_owner is not None and _ptt_owner != sid:
        await sio.emit('tone_1750_status', {'ok': False, 'error': 'PTT locked'}, to=sid)
        return

    auto_release = _ptt_owner is None
    if auto_release:
        if _ptt_drain_task and not _ptt_drain_task.done():
            _ptt_drain_task.cancel()
            _ptt_drain_task = None
        _ptt_owner = sid
        radio.send_key(16)
        await asyncio.sleep(0.1)
        await gpio_manager.on_ptt(True)
        await sio.emit('ptt_status', {'active': True, 'holder': sid, 'user': user})
        if lcd:
            await asyncio.sleep(0.15)
            lcd.force_flush()

    _tone_1750_busy = True
    try:
        await asyncio.to_thread(radio.send_1750_tone)
        log_activity(user, 'TONE_1750', 'auto_ptt' if auto_release else 'held_ptt')
        if auto_release and _ptt_owner == sid:
            await gpio_manager.on_ptt(False)
            radio.send_key(13)
            _ptt_owner = None
            await sio.emit('ptt_status', {'active': False, 'holder': None, 'user': None})
            if lcd:
                await asyncio.sleep(0.15)
                lcd.force_flush()
        await sio.emit('tone_1750_status', {'ok': True}, to=sid)
    except Exception as exc:
        logger.exception('1750 Hz tone failed')
        if auto_release and _ptt_owner == sid:
            await gpio_manager.on_ptt(False)
            radio.send_key(13)
            _ptt_owner = None
            await sio.emit('ptt_status', {'active': False, 'holder': None, 'user': None})
        await sio.emit('tone_1750_status', {'ok': False, 'error': str(exc)}, to=sid)
    finally:
        _tone_1750_busy = False


@sio.event
async def ptt_off(sid, data=None):
    global _ptt_owner, _ptt_drain_task
    if _ptt_owner != sid:
        return
    _ptt_drain_task = asyncio.create_task(_drain_and_release(sid))
    logger.info(f"PTT UP – drain started (sid={sid})")


async def _drain_and_release(sid):
    global _ptt_owner, _ptt_drain_task
    try:
        await asyncio.sleep(_PTT_DRAIN_DELAY)
        logger.info("PTT drain complete, releasing")
        await gpio_manager.on_ptt(False)
    except asyncio.CancelledError:
        logger.info("PTT drain cancelled (re-pressed)")
        return
    finally:
        _ptt_drain_task = None
    if _ptt_owner == sid:
        if radio:
            radio.send_key(13)
        _ptt_owner = None
        await sio.emit('ptt_status', {'active': False, 'holder': None, 'user': None})
        if lcd:
            await asyncio.sleep(0.15)
            lcd.force_flush()


@sio.event
async def request_rssi(sid, data=None):
    user = _sid_users.get(sid)
    if user:
        touch_activity(user)
    if radio and radio.connected:
        logger.info("RSSI requested")
        radio.request_rssi()
