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
        # Frequency read-on-demand (no continuous polling)
        self._freq_reg38 = None
        self._freq_reg39 = None
        self._freq_future = None
        self._freq_seq = 0
        self._eeprom_mode = False

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

    def enter_eeprom_mode(self):
        """Pause Remote UI and switch to raw serial for EEPROM access.
        Stops the reader thread and reopens port without Hello."""
        import time as _time
        # Stop reader thread
        self._running = False
        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=2.0)
        # Close and reopen port without Hello/init
        try:
            if self.port and self.port.is_open:
                self.port.close()
        except Exception:
            pass
        _time.sleep(0.1)
        self.port = serial.Serial(
            port=self.port_name, baudrate=SERIAL_BAUD,
            parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE,
            bytesize=serial.EIGHTBITS, timeout=1.0, write_timeout=10,
        )
        # Drain all pending UI data thoroughly
        for _ in range(5):
            _time.sleep(0.1)
            try:
                self.port.read(4096)
            except Exception:
                pass
        self._eeprom_mode = True

    def exit_eeprom_mode(self):
        """Restore Remote UI mode after EEPROM access.
        Reconnects with Hello sequence and restarts reader thread."""
        import time as _time
        self._eeprom_mode = False
        # Close and do full reconnect
        try:
            if self.port and self.port.is_open:
                self.port.close()
        except Exception:
            pass
        _time.sleep(0.1)
        # Reconnect with full init (same as connect())
        self.port = serial.Serial(
            port=self.port_name, baudrate=SERIAL_BAUD,
            parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE,
            bytesize=serial.EIGHTBITS, timeout=SERIAL_TIMEOUT, write_timeout=10,
        )
        _time.sleep(0.05)
        try:
            self.port.write(b"\x00")
            _time.sleep(0.1)
            self.port.read(4096)
        except Exception:
            pass
        # Restart reader thread
        self._running = True
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()
        _time.sleep(0.05)
        # Re-activate Remote UI
        self.send_hello()
        _time.sleep(0.5)
        self.send_key(10)  # MENU
        _time.sleep(0.1)
        self.send_key(13)  # EXIT
        _time.sleep(0.2)

    def eeprom_read_chunk(self, offset: int, size: int = 128, timeout: float = 3.0):
        """Read a single EEPROM chunk in raw mode. Must be in eeprom_mode.
        Returns bytes or None on timeout."""
        from backend.radio.protocol import Packet, build_packet, u16, PacketParser
        import time as _time

        pkt = build_packet(Packet.READ_EEPROM, u16(offset), u16(size), 0x12345678)
        self.port.write(pkt)
        self.port.flush()

        # Collect raw bytes and parse
        buf = bytearray()
        deadline = _time.time() + timeout
        while _time.time() < deadline:
            chunk = self.port.read(256)
            if chunk:
                buf.extend(chunk)
            # Try to parse for our reply
            result = [None]
            def on_cmd(data):
                if len(data) >= 2:
                    cmd = data[0] | (data[1] << 8)
                    if cmd == Packet.READ_EEPROM_REPLY and len(data) >= 9:
                        resp_offset = data[4] | (data[5] << 8)
                        resp_size = data[6]
                        if resp_offset == offset:
                            result[0] = bytes(data[8:8 + resp_size])
            parser = PacketParser()
            parser.feed(bytes(buf), on_command=on_cmd, on_ui=lambda *a: None)
            if result[0] is not None:
                return result[0]

        # Retry once if first attempt fails (common for first chunk after mode switch)
        if result[0] is None:
            pkt = build_packet(Packet.READ_EEPROM, u16(offset), u16(size), 0x12345678)
            self.port.write(pkt)
            self.port.flush()
            buf.clear()
            deadline = _time.time() + timeout
            while _time.time() < deadline:
                chunk = self.port.read(256)
                if chunk:
                    buf.extend(chunk)
                result[0] = None
                def on_cmd2(data):
                    if len(data) >= 2:
                        cmd = data[0] | (data[1] << 8)
                        if cmd == Packet.READ_EEPROM_REPLY and len(data) >= 9:
                            resp_offset = data[4] | (data[5] << 8)
                            resp_size = data[6]
                            if resp_offset == offset:
                                result[0] = bytes(data[8:8 + resp_size])
                parser2 = PacketParser()
                parser2.feed(bytes(buf), on_command=on_cmd2, on_ui=lambda *a: None)
                if result[0] is not None:
                    return result[0]
        return None  # Timeout

    def eeprom_write_chunk(self, offset: int, data: bytes, timeout: float = 3.0):
        """Write a single EEPROM chunk in raw mode. Must be in eeprom_mode.
        Returns True on success, False on timeout."""
        from backend.radio.protocol import Packet, build_packet, u16, PacketParser
        import time as _time

        pkt = build_packet(Packet.WRITE_EEPROM, u16(offset), 1, 0x12345678, data)
        self.port.write(pkt)
        self.port.flush()

        buf = bytearray()
        deadline = _time.time() + timeout
        while _time.time() < deadline:
            chunk = self.port.read(256)
            if chunk:
                buf.extend(chunk)
            result = [False]
            def on_cmd(raw_data):
                if len(raw_data) >= 2:
                    cmd = raw_data[0] | (raw_data[1] << 8)
                    if cmd == Packet.WRITE_EEPROM_REPLY and len(raw_data) >= 6:
                        resp_offset = raw_data[4] | (raw_data[5] << 8)
                        if resp_offset == offset:
                            result[0] = True
            parser = PacketParser()
            parser.feed(bytes(buf), on_command=on_cmd, on_ui=lambda *a: None)
            if result[0]:
                return True

        return False  # Timeout

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
        """No-op placeholder – frequency is read on demand via read_frequency()."""
        log.info("RSSI poll loop idle (no continuous polling)")
        while self._running and self.connected:
            try:
                time.sleep(1)
            except Exception:
                pass
        log.info("RSSI poll loop stopped")

    async def read_frequency(self, timeout_s: float = 0.5):
        """Read TX frequency from BK4819 regs 0x38+0x39 on demand.
        Reads registers sequentially, matching by sequence number.
        Returns frequency string e.g. '144.76250' or None on timeout."""
        if not self.connected:
            return None
        self._freq_seq += 1
        seq = self._freq_seq
        self._freq_reg38 = None
        self._freq_reg39 = None
        self._freq_future = asyncio.get_event_loop().create_future()
        self._freq_future_seq = seq
        self.read_register(0x38)
        await asyncio.sleep(0.05)
        self.read_register(0x39)
        try:
            return await asyncio.wait_for(self._freq_future, timeout=timeout_s)
        except asyncio.TimeoutError:
            if self._freq_future_seq == seq:
                log.warning("read_frequency() timed out after %.1fs", timeout_s)
                self._freq_future = None
            return None

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
                # Collect BK4819 regs 0x38+0x39 for frequency read
                # Only accept registers when we have an active future
                if reg == 0x38 and self._freq_future and not self._freq_future.done():
                    self._freq_reg38 = val
                elif reg == 0x39 and self._freq_future and not self._freq_future.done():
                    self._freq_reg39 = val
                if self._freq_reg38 is not None and self._freq_reg39 is not None:
                    raw = (self._freq_reg39 << 16) | self._freq_reg38
                    freq_hz = raw * 10
                    mhz = f"{freq_hz / 1e6:.5f}" if freq_hz > 0 else None
                    log.info(f"Freq from regs: {mhz} MHz (raw=0x{raw:08X})")
                    if self._freq_future and not self._freq_future.done():
                        self._freq_future.set_result(mhz)
                    self._freq_reg38 = None
                    self._freq_reg39 = None
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
