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

from backend.audio.rx_pipeline import RxPipeline
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
_pending_ptt_cancel: set[str] = set()
_last_firmware_smeter_at: float = 0.0
_FIRMWARE_SMETER_TTL = 0.5  # prefer firmware UI type 8 when recent


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


def init_radio(rx_audio: Optional[RxPipeline] = None):
    global radio, lcd, _last_firmware_smeter_at
    radio = RadioConnection()
    lcd = LCDDisplay()
    _last_firmware_smeter_at = 0.0

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
        prev_meter = lcd.meter_s_raw
        lcd.process_ui_packet(ui_type, val1, val2, val3, data_len, data)
        freq = lcd.parse_active_vfo_frequency_mhz()
        if freq is not None:
            radio.set_rx_band_from_mhz(freq)
        if ui_type == 8:
            _last_firmware_smeter_at = time.monotonic()
            await _emit_smeter(lcd.meter_s_raw)
        elif lcd.meter_s_raw != prev_meter:
            _last_firmware_smeter_at = time.monotonic()
            await _emit_smeter(lcd.meter_s_raw)
        elif ui_type == 6:
            lcd.flush()

    async def on_hw_rssi(dbm: int, raw: int, s_raw: float, squelch_open: bool):
        if rx_audio is not None:
            rx_audio.update_squelch_open(squelch_open)
        # S-meter: firmware UI type 8 / CRT text only — no raw BK4819 fallback

    radio.on_ui = handle_ui
    radio.on_rssi_update = on_hw_rssi
    radio.on_register = _on_register
    radio.on_connect = _on_radio_connect
    radio.on_disconnect = _on_radio_disconnect

    return radio


def get_sio_app():
    return socketio.ASGIApp(sio, socketio_path='socket.io')


# ─── Emit helpers ─────────────────────────────────────────────────

async def _emit_smeter(s_raw: float) -> None:
    await sio.emit('lcd_update', {
        'rssi_s_raw': s_raw,
        'rssi_hw': True,
    })


async def _emit_lcd(state):
    await sio.emit('lcd_update', state)


async def _expire_ptt_cancel(sid: str) -> None:
    await asyncio.sleep(1.0)
    _pending_ptt_cancel.discard(sid)


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
    global _ptt_owner, _ptt_drain_task, _pending_ptt_cancel
    user = _sid_users.get(sid)
    if not user:
        return
    if check_timeout(user):
        await sio.disconnect(sid)
        return
    touch_activity(user)

    if sid in _pending_ptt_cancel:
        _pending_ptt_cancel.discard(sid)
        logger.info("PTT ON ignored — client already released (sid=%s)", sid)
        return

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
    global _ptt_owner, _ptt_drain_task, _pending_ptt_cancel
    user = _sid_users.get(sid)

    if _ptt_owner is None:
        _pending_ptt_cancel.add(sid)
        asyncio.create_task(_expire_ptt_cancel(sid))
        if radio and radio.connected:
            radio.send_key(13)
        logger.info("PTT OFF early — cancel pending ON (sid=%s)", sid)
        return

    if _ptt_owner != sid:
        owner_user = _sid_users.get(_ptt_owner)
        if not user or owner_user != user:
            return
        _ptt_owner = sid

    if _ptt_drain_task and not _ptt_drain_task.done():
        _ptt_drain_task.cancel()
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
