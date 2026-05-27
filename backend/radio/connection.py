"""Serial connection manager for the Quansheng UV-K5.

Handles:
- Serial port connection with auto-reconnect
- Packet sending and receiving (threaded reader to avoid blocking asyncio)
- Connection lifecycle (heartbeat, init sequence)
"""

import asyncio
import logging
import time
import threading
from typing import Optional, Callable

import serial

from backend.config import get
from backend.radio.protocol import (
    PacketParser, Packet, build_key_press,
    build_hello, build_get_rssi, build_get_screen,
    Cmd, Key,
)
from backend.radio.adapter import RadioAdapter, RadioInfo, RadioState

logger = logging.getLogger(__name__)


class QuanshengAdapter(RadioAdapter):
    """Radio adapter for Quansheng UV-K5 via serial connection."""
    
    def __init__(self):
        self._serial: Optional[serial.Serial] = None
        self._state = RadioState.DISCONNECTED
        self._parser = PacketParser()
        self._info = RadioInfo()
        self._connected_since = 0.0
        self._reader_thread: Optional[threading.Thread] = None
        self._running = False
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        
        # Wire up parser callbacks
        self._parser.on_packet = self._handle_packet
        self._parser.on_ui_data = self._handle_ui_data
        
        # Config
        self._device = get("radio.device", "/dev/ttyACM0")
        self._baudrate = get("radio.baudrate", 38400)
        self._timeout = get("radio.timeout", 1.0)
        self._reconnect_delay = get("radio.reconnect_delay", 3.0)
    
    @property
    def state(self) -> RadioState:
        return self._state
    
    def _set_state(self, new_state: RadioState) -> None:
        if self._state != new_state:
            old = self._state
            self._state = new_state
            self._info.state = new_state
            logger.info(f"Radio state: {old.value} → {new_state.value}")
            if self._state_callback and self._loop:
                # Schedule callback on the event loop (we might be in a thread)
                self._loop.call_soon_threadsafe(
                    lambda: asyncio.ensure_future(self._safe_callback(self._state_callback, new_state))
                )
    
    async def _safe_callback(self, cb, *args):
        """Safely call an async or sync callback."""
        try:
            result = cb(*args)
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            logger.error(f"Callback error: {e}")
    
    # ─── Connection ───────────────────────────────────────────────
    
    async def connect(self) -> bool:
        """Open serial port and initialize the radio connection."""
        try:
            self._set_state(RadioState.CONNECTING)
            self._loop = asyncio.get_event_loop()
            
            self._serial = serial.Serial(
                port=self._device,
                baudrate=self._baudrate,
                timeout=1.0,  # Must be >=1s for reliable reads
                write_timeout=1.0,
            )
            
            logger.info(f"Serial port opened: {self._device} @ {self._baudrate} baud")
            
            self._set_state(RadioState.CONNECTED)
            self._connected_since = time.time()
            self._running = True
            
            # Start reader thread FIRST so we catch the init response
            self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
            self._reader_thread.start()
            
            # Now send init sequence
            self._serial.write(b'\x00')
            await asyncio.sleep(0.1)
            
            self._serial.write(build_hello())  # Enables remote UI mode
            await asyncio.sleep(0.2)
            
            self._serial.write(build_key_press(Key.MENU))
            await asyncio.sleep(0.1)
            self._serial.write(build_key_press(Key.EXIT))
            await asyncio.sleep(0.1)
            
            # Start async heartbeat
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            
            return True
            
        except serial.SerialException as e:
            logger.error(f"Failed to connect to radio: {e}")
            self._set_state(RadioState.ERROR)
            return False
    
    async def disconnect(self) -> None:
        """Close the serial connection and stop all background tasks."""
        self._running = False
        
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
        
        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=2.0)
        
        if self._serial and self._serial.is_open:
            self._serial.close()
            logger.info("Serial port closed")
        
        self._set_state(RadioState.DISCONNECTED)
    
    # ─── Reader Thread ────────────────────────────────────────────
    
    def _reader_loop(self) -> None:
        """Background thread: continuously read from serial port."""
        logger.info("Serial reader thread started")
        bytes_total = 0
        error_count = 0
        
        while self._running and self._serial and self._serial.is_open:
            try:
                data = self._serial.read(4096)
                if data:
                    bytes_total += len(data)
                    logger.info(f"Serial: {len(data)} bytes (total: {bytes_total}), first: {data[:10].hex()}")
                    self._parser.feed(data)
                    error_count = 0
                # else: timeout, no data, just loop
            except serial.SerialException as e:
                error_count += 1
                logger.error(f"Serial read error ({error_count}): {e}")
                if error_count > 5 or not self._running:
                    break
            except Exception as e:
                error_count += 1
                logger.error(f"Reader error ({error_count}): {e}")
                if error_count > 5:
                    break
        
        logger.info(f"Serial reader stopped (total bytes read: {bytes_total})")
    
    # ─── Heartbeat ────────────────────────────────────────────────
    
    async def _heartbeat_loop(self) -> None:
        """Send periodic Hello packets to keep the connection alive."""
        try:
            while self._running and self._state == RadioState.CONNECTED:
                self._send_raw(build_hello())
                await asyncio.sleep(5.0)
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.error(f"Heartbeat error: {e}")
    
    # ─── Send Helpers ─────────────────────────────────────────────
    
    def _send_raw(self, data: bytes) -> None:
        """Send raw bytes to serial port (thread-safe)."""
        if self._serial and self._serial.is_open:
            try:
                self._serial.write(data)
            except serial.SerialException as e:
                logger.error(f"Serial write error: {e}")
    
    # ─── Packet Handlers (called from reader thread) ──────────────
    
    def _handle_packet(self, packet: Packet) -> None:
        """Handle a parsed binary protocol packet from the radio."""
        cmd = packet.cmd
        
        if cmd == Cmd.RSSI_INFO:
            rssi_raw = int.from_bytes(packet.params[:2], 'little')
            dbm = -(rssi_raw & 0x3FF) / 2.0
            
            # S-unit conversion
            s_points = [
                (-121, "S1"), (-115, "S2"), (-109, "S3"), (-103, "S4"),
                (-97, "S5"), (-91, "S6"), (-85, "S7"), (-79, "S8"), (-73, "S9"),
            ]
            s_unit = "S9+"
            for threshold, label in s_points:
                if dbm <= threshold:
                    s_unit = label
                    break
            
            self._info.rssi_dbm = dbm
            self._info.s_unit = s_unit
            
            if self._rssi_callback and self._loop:
                self._loop.call_soon_threadsafe(
                    lambda: asyncio.ensure_future(
                        self._safe_callback(self._rssi_callback, dbm, s_unit)
                    )
                )
        
        elif cmd == Cmd.IM_HERE:
            logger.debug("Radio acknowledged heartbeat")
        
        else:
            logger.debug(f"Packet: cmd=0x{cmd:04X} params={packet.params.hex()}")
    
    def _handle_ui_data(self, data: bytes) -> None:
        """Handle a UI text rendering packet from the radio."""
        logger.debug(f"UI data: type={data[0] if data else '?'} len={len(data)}")
        
        if self._display_callback and self._loop:
            data_list = list(data)
            self._loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(
                    self._safe_callback(self._display_callback, data_list)
                )
            )
    
    # ─── RadioAdapter Interface ───────────────────────────────────
    
    async def get_info(self) -> RadioInfo:
        return self._info
    
    async def send_key(self, keycode: int, hold: bool = False) -> None:
        self._send_raw(build_key_press(keycode))
    
    async def set_ptt(self, active: bool) -> None:
        if active:
            self._info.is_transmitting = True
            self._send_raw(build_key_press(Key.PTT))
        else:
            self._info.is_transmitting = False
            self._send_raw(build_key_press(Key.EXIT))
    
    async def get_rssi(self) -> float:
        self._send_raw(build_get_rssi())
        return self._info.rssi_dbm
    
    async def request_display(self) -> None:
        self._send_raw(build_get_screen())
