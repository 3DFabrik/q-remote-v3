"""Radio adapter interface.

Defines the abstract interface that all radio adapters must implement.
Currently only QuanshengAdapter (UV-K5 serial), but designed for future
HamlibAdapter or other radio backends.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable


class RadioState(Enum):
    """Connection state to the radio hardware."""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


@dataclass
class RadioInfo:
    """Current radio status information."""
    state: RadioState = RadioState.DISCONNECTED
    frequency_hz: int = 0           # e.g. 145500000 for 145.5 MHz
    battery_voltage: float = 0.0
    rssi_dbm: float = 0.0
    s_unit: str = ""
    is_transmitting: bool = False
    display_data: list = field(default_factory=list)  # LCD rendering commands


class RadioAdapter(ABC):
    """Abstract base class for radio adapters.
    
    All radio adapters (UV-K5 serial, Hamlib, etc.) must implement this interface.
    The backend only talks to this interface, never to specific hardware.
    """

    @abstractmethod
    async def connect(self) -> bool:
        """Establish connection to the radio. Returns True on success."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from the radio cleanly."""
        ...

    @abstractmethod
    async def get_info(self) -> RadioInfo:
        """Get current radio status."""
        ...

    @abstractmethod
    async def send_key(self, keycode: int, hold: bool = False) -> None:
        """Send a key press to the radio.
        
        Args:
            keycode: Key code (0-19, see protocol docs)
            hold: If True, send as long-press (repeated)
        """
        ...

    @abstractmethod
    async def set_ptt(self, active: bool) -> None:
        """Engage or release PTT (Push-To-Talk).
        
        Args:
            active: True = start transmitting, False = stop
        """
        ...

    @abstractmethod
    async def get_rssi(self) -> float:
        """Request RSSI reading. Returns dBm value."""
        ...

    @abstractmethod
    async def request_display(self) -> None:
        """Request a screen dump from the radio."""
        ...

    def on_state_change(self, callback: Callable[[RadioState], None]) -> None:
        """Register a callback for connection state changes."""
        self._state_callback = callback

    def on_display_update(self, callback: Callable[[list], None]) -> None:
        """Register a callback for display data updates."""
        self._display_callback = callback

    def on_rssi_update(self, callback: Callable[[float, str], None]) -> None:
        """Register a callback for RSSI updates (dbm, s_unit)."""
        self._rssi_callback = callback

    # Callback storage (shared by all adapters)
    _state_callback: Callable = None
    _display_callback: Callable = None
    _rssi_callback: Callable = None
