"""Serial connection to Quansheng UV-K5 radio.
Ported from V1's working RadioConnection with async SocketIO callbacks.
"""

import threading
import time
import asyncio
import logging
from typing import Optional

import serial

from backend.config import get
from backend.radio.protocol import (
    PacketParser, Packet, build_packet, u16,
)

log = logging.getLogger(__name__)

SERIAL_BAUD = 38400
SERIAL_TIMEOUT = 0.1


class RadioConnection:
    """Direct port of V1's RadioConnection with async callback support."""

    def __init__(self):
        self.port_name = get("radio.device", "/dev/ttyACM0")
        self.port = None
        self.connected = False
        self._lock = threading.Lock()
        self._reader_thread = None
        self._running = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self.parser = PacketParser()

        # Callbacks (set by socketio_server.py)
        self.on_ui = None         # async (ui_type, v1, v2, v3, dlen, data)
        self.on_rssi = None       # async (data)
        self.on_register = None   # async (reg, val)
        self.on_command = None    # async (data)
        self.on_connect = None    # async ()
        self.on_disconnect = None # async ()

        # RSSI polling
        self._rssi_thread = None
        self._rssi_interval = 0.2  # 200ms

    def connect(self):
        try:
            self.port = serial.Serial(
                port=self.port_name, baudrate=SERIAL_BAUD,
                parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE,
                bytesize=serial.EIGHTBITS, timeout=SERIAL_TIMEOUT, write_timeout=10,
            )
            self.connected = True
            self._loop = asyncio.get_event_loop()
            log.info(f"Connected to radio on {self.port_name}")

            # V1 init sequence exactly
            time.sleep(0.05)
            try:
                self.port.write(b'\x00')
                time.sleep(0.1)
                self.port.read(4096)
            except Exception as e:
                log.warning(f"Init \\x00 failed (non-critical): {e}")

            # Start reader BEFORE Hello so we don't miss initial display data
            self._running = True
            self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
            self._reader_thread.start()
            time.sleep(0.05)

            log.info("Sending Hello with magic timestamp to activate Remote UI...")
            self.send_hello()
            time.sleep(0.5)

            # V1 key codes: 10=MENU, 13=EXIT
            self.send_key(10)  # MENU - triggers display redraw
            time.sleep(0.1)
            self.send_key(13)  # EXIT - go back to main screen
            time.sleep(0.2)

            log.info("Radio init complete - Remote UI active")
            self._safe_emit('on_connect')
            # Start RSSI polling (BK4819 register 0x67)
            self._rssi_thread = threading.Thread(target=self._rssi_poll_loop, daemon=True)
            self._rssi_thread.start()
            return True
        except Exception as e:
            log.error(f"Failed to connect to radio: {e}")
            self.connected = False
            return False

    def disconnect(self):
        self._running = False
        self.connected = False
        # RSSI poll thread will exit via self._running check
        try:
            if self.port and self.port.is_open:
                self.port.close()
        except Exception:
            pass
        self._safe_emit('on_disconnect')

    def send_hello(self):
        self.send_command(Packet.HELLO, 0x12345678)

    def send_command(self, cmd, *args):
        if not self.connected or not self.port:
            return
        pkt = build_packet(cmd, *args)
        with self._lock:
            try:
                self.port.write(pkt)
                self.port.flush()
            except Exception as e:
                log.error(f"Send error: {e}")
                self.connected = False

    def send_key(self, key_code):
        self.send_command(Packet.KEY_PRESS, u16(key_code), 0x12345678)

    def request_rssi(self):
        # Try both methods: GET_RSSI and register 0x67
        self.send_command(Packet.GET_RSSI, 0x12345678)

    def read_register(self, reg):
        self.send_command(Packet.READ_REGISTERS, u16(1), u16(reg))

    def request_screen(self):
        self.send_command(Packet.GET_SCREEN, 0x12345678)

    def _rssi_poll_loop(self):
        """Periodically poll BK4819 register 0x67 for RSSI."""
        log.info("RSSI poll loop started (200ms interval)")
        while self._running and self.connected:
            try:
                self.read_register(0x67)
                time.sleep(self._rssi_interval)
            except Exception as e:
                if self._running:
                    log.error(f"RSSI poll error: {e}")
                    time.sleep(1)
        log.info("RSSI poll loop stopped")

    def _reader_loop(self):
        while self._running:
            try:
                if self.port and self.port.is_open:
                    raw = self.port.read(256)
                    if raw:
                        log.info(f"Serial read {len(raw)} bytes: {raw[:40].hex()}")
                        self.parser.feed(
                            raw,
                            on_command=self._on_command,
                            on_ui=self._on_ui_callback,
                        )
                else:
                    time.sleep(0.1)
            except serial.SerialException as e:
                log.error(f"Serial read error: {e}")
                self.connected = False
                self._safe_emit('on_disconnect')
                break
            except Exception as e:
                log.error(f"Reader error: {e}")

    def _on_command(self, data):
        if len(data) < 2:
            return
        cmd = data[0] | (data[1] << 8)
        if cmd == Packet.RSSI_INFO:
            log.debug(f"RSSI cmd received (deprecated): {data.hex()}")
            self._safe_emit('on_rssi', data)
        elif cmd == Packet.REGISTER_INFO:
            # RegisterInfo: [cmd:2] [paramLen:2] [reg:2] [val:2]
            log.info(f"RegisterInfo cmd=0x{cmd:04X} raw: {data.hex()}")
            if len(data) >= 8:
                param_len = data[2] | (data[3] << 8)
                reg = data[4] | (data[5] << 8)
                val = data[6] | (data[7] << 8)
                log.info(f"Register parsed: reg=0x{reg:04X} val=0x{val:04X} (paramLen={param_len})")
                self._safe_emit('on_register', reg, val)
        elif cmd == Packet.IM_HERE:
            log.debug("Radio acknowledged heartbeat")
        else:
            log.debug(f"Cmd: 0x{cmd:04X} ({len(data)} bytes)")

    def _on_ui_callback(self, ui_type, val1, val2, val3, data_len, data):
        if not self._loop or not self.on_ui:
            return
        self._loop.call_soon_threadsafe(
            lambda: asyncio.ensure_future(
                self._async_ui(ui_type, val1, val2, val3, data_len, data)
            )
        )

    async def _async_ui(self, ui_type, val1, val2, val3, data_len, data):
        if self.on_ui:
            try:
                await self.on_ui(ui_type, val1, val2, val3, data_len, data)
            except Exception as e:
                log.error(f"UI callback error: {e}")

    def _safe_emit(self, callback_name, *args):
        """Call an async callback on the event loop from the reader thread."""
        cb = getattr(self, callback_name, None)
        if cb and self._loop:
            self._loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(self._async_call(cb, *args))
            )

    async def _async_call(self, cb, *args):
        try:
            result = cb(*args)
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            log.error(f"Async callback error: {e}")
