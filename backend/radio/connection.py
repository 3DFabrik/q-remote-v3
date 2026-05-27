"""Serial connection manager for the Quansheng UV-K5.

Handles:
- Serial port connection with auto-reconnect
- Packet sending and receiving
- Connection lifecycle (heartbeat, init sequence)
"""

import asyncio
import logging
import time
from typing import Optional, Callable

import serial

from backend.config import get
from backend.radio.protocol import (
    PacketParser, Packet, build_packet, build_key_press,
    build_hello, build_get_screen, build_get_rssi,
    Cmd, Key, xor_crypt,
)
from backend.radio.adapter import RadioAdapter, RadioInfo, RadioState

logger = logging.getLogger(__name__)


class QuanshengAdapter(RadioAdapter):
    """Radio adapter for Quansheng UV-K5 via serial connection.
    
    Manages the serial connection, sends commands, parses responses,
    and provides auto-reconnect on disconnection.
    
    Usage:
        adapter = QuanshengAdapter()
        adapter.on_display_update = lambda data: ...
        adapter.on_rssi_update = lambda dbm, s_unit: ...
        
        await adapter.connect()
        info = await adapter.get_info()
    """
    
    def __init__(self):
        self._serial: Optional[serial.Serial] = None
        self._state = RadioState.DISCONNECTED
        self._parser = PacketParser()
        self._info = RadioInfo()
        self._last_heartbeat = 0.0
        self._connected_since = 0.0
        self._reconnect_task: Optional[asyncio.Task] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        
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
            if self._state_callback:
                self._state_callback(new_state)
    
    # ─── Connection ───────────────────────────────────────────────
    
    async def connect(self) -> bool:
        """Open serial port and initialize the radio connection."""
        try:
            self._set_state(RadioState.CONNECTING)
            
            self._serial = serial.Serial(
                port=self._device,
                baudrate=self._baudrate,
                timeout=self._timeout,
                write_timeout=self._timeout,
            )
            
            logger.info(f"Serial port opened: {self._device} @ {self._baudrate} baud")
            
            # Init sequence: test byte, then Menu + Exit to clear state
            self._serial.write(b'\x00')
            await asyncio.sleep(0.1)
            self._serial.write(build_key_press(Key.MENU))
            await asyncio.sleep(0.1)
            self._serial.write(build_key_press(Key.EXIT))
            await asyncio.sleep(0.1)
            
            self._set_state(RadioState.CONNECTED)
            self._connected_since = time.time()
            
            # Start background tasks
            self._reader_task = asyncio.create_task(self._read_loop())
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            
            return True
            
        except serial.SerialException as e:
            logger.error(f"Failed to connect to radio: {e}")
            self._set_state(RadioState.ERROR)
            # Start auto-reconnect
            self._reconnect_task = asyncio.create_task(self._reconnect_loop())
            return False
    
    async def disconnect(self) -> None:
        """Close the serial connection and stop all background tasks."""
        # Cancel background tasks
        for task in [self._reader_task, self._heartbeat_task, self._reconnect_task]:
            if task and not task.done():
                task.cancel()
        
        if self._serial and self._serial.is_open:
            self._serial.close()
            logger.info("Serial port closed")
        
        self._set_state(RadioState.DISCONNECTED)
    
    async def _reconnect_loop(self) -> None:
        """Periodically attempt to reconnect."""
        while self._state != RadioState.CONNECTED:
            try:
                await asyncio.sleep(self._reconnect_delay)
                logger.info("Attempting reconnect...")
                if await self.connect():
                    logger.info("Reconnected successfully")
                    return
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error(f"Reconnect failed: {e}")
    
    # ─── Background Tasks ─────────────────────────────────────────
    
    async def _read_loop(self) -> None:
        """Continuously read from serial port and parse incoming data."""
        try:
            while self._state == RadioState.CONNECTED and self._serial:
                if self._serial.in_waiting > 0:
                    data = self._serial.read(self._serial.in_waiting)
                    self._parser.feed(data)
                else:
                    await asyncio.sleep(0.01)  # 10ms poll interval
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.error(f"Serial read error: {e}")
            self._set_state(RadioState.ERROR)
            self._reconnect_task = asyncio.create_task(self._reconnect_loop())
    
    async def _heartbeat_loop(self) -> None:
        """Send periodic Hello packets to keep the connection alive."""
        try:
            while self._state == RadioState.CONNECTED:
                self._send_raw(build_hello())
                self._last_heartbeat = time.time()
                await asyncio.sleep(5.0)  # Heartbeat every 5 seconds
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.error(f"Heartbeat error: {e}")
    
    # ─── Send Helpers ─────────────────────────────────────────────
    
    def _send_raw(self, data: bytes) -> None:
        """Send raw bytes to serial port."""
        if self._serial and self._serial.is_open:
            try:
                self._serial.write(data)
            except serial.SerialException as e:
                logger.error(f"Serial write error: {e}")
                self._set_state(RadioState.ERROR)
    
    # ─── Packet Handlers ──────────────────────────────────────────
    
    def _handle_packet(self, packet: Packet) -> None:
        """Handle a parsed binary protocol packet from the radio."""
        cmd = packet.cmd
        
        if cmd == Cmd.RSSI_INFO:
            rssi_raw = int.from_bytes(packet.params[:2], 'little')
            dbm = rssi_to_dbm_safe(rssi_raw)
            self._info.rssi_dbm = dbm
            self._info.s_unit = dbm_to_s_unit_safe(dbm)
            if self._rssi_callback:
                self._rssi_callback(dbm, self._info.s_unit)
        
        elif cmd == Cmd.REGISTER_INFO:
            logger.debug(f"Register info: {packet.params.hex()}")
        
        elif cmd == Cmd.IM_HERE:
            logger.debug("Radio acknowledged heartbeat")
        
        else:
            logger.debug(f"Unhandled packet: cmd=0x{cmd:04X} params={packet.params.hex()}")
    
    def _handle_ui_data(self, data: bytes) -> None:
        """Handle a UI text rendering packet from the radio."""
        # Forward to display callback
        if self._display_callback:
            self._display_callback(data)
    
    # ─── RadioAdapter Interface ───────────────────────────────────
    
    async def get_info(self) -> RadioInfo:
        """Get current radio status."""
        return self._info
    
    async def send_key(self, keycode: int, hold: bool = False) -> None:
        """Send a key press to the radio."""
        self._send_raw(build_key_press(keycode))
    
    async def set_ptt(self, active: bool) -> None:
        """Engage or release PTT.
        
        For Standard Mode: KeyPress(16) for TX, KeyPress(19) for release.
        """
        if active:
            self._info.is_transmitting = True
            self._send_raw(build_key_press(Key.PTT))
        else:
            self._info.is_transmitting = False
            self._send_raw(build_key_press(Key.EXIT))
    
    async def get_rssi(self) -> float:
        """Request RSSI and return last known value."""
        self._send_raw(build_get_rssi())
        return self._info.rssi_dbm
    
    async def request_display(self) -> None:
        """Request a screen dump from the radio."""
        self._send_raw(build_get_screen())


# ─── Helper functions (avoid circular imports) ────────────────────

def rssi_to_dbm_safe(rssi_raw: int) -> float:
    from backend.radio.protocol import rssi_to_dbm
    return rssi_to_dbm(rssi_raw)

def dbm_to_s_unit_safe(dbm: float) -> str:
    from backend.radio.protocol import dbm_to_s_unit
    return dbm_to_s_unit(dbm)
