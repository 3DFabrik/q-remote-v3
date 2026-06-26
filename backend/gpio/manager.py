"""GPIO Manager for Q-Remote V3.

Provides fail-safe GPIO control via gpiozero.
Supports PTT sequencing, header buttons, session-bound safety,
and fail-safe cleanup on shutdown/crash.

Spec: docs/SPEC-GPIO.md
"""

import asyncio
import logging
import os
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ─── Constants ─────────────────────────────────────────────────────

# BCM pins reserved for system functions (never available to user)
SYSTEM_RESERVED = {2, 3}  # I2C bus

# UART pins – reserved (serial console / Bluetooth)
UART_PINS = {14, 15}  # TXD, RXD

# DS18B20 1-Wire – only BCM 4 (physical pin 7) supports 1-Wire
DS18B20_PIN = 4

# Soft-reserved: pins with special hardware functions
# (SPI0, PWM, PCM/I2S) — excluded from output for safety
SOFT_RESERVED = {7, 8, 9, 10, 11,  # SPI0 (CE1, CE0, MISO, MOSI, SCLK)
                 12, 13,           # Hardware PWM0, PWM1
                 18, 19, 20, 21}   # PCM/I2S (CLK, FS, DIN, DOUT)

# All hard-reserved (system + UART)
HARD_RESERVED = SYSTEM_RESERVED | UART_PINS

# Everything that's NOT available for output
ALL_RESERVED = HARD_RESERVED | SOFT_RESERVED | {DS18B20_PIN}

# All user-accessible BCM GPIO pins on 40-pin header
ALL_PINS = [
    4, 5, 6, 7, 8, 9, 10, 11, 12, 13,
    16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27,
]

# Pins available for OUTPUT use
OUTPUT_PINS = [p for p in ALL_PINS if p not in ALL_RESERVED]

# Trigger type constants (match spec column 3)
TRIGGER_PTT = "ptt"
TRIGGER_BUTTON_1 = "button1"
TRIGGER_BUTTON_2 = "button2"
TRIGGER_TEMP = "temp"

ALL_TRIGGERS = [
    TRIGGER_PTT, TRIGGER_BUTTON_1, TRIGGER_BUTTON_2,
    TRIGGER_TEMP,
]

TRIGGER_LABELS = {
    TRIGGER_PTT: "On TX (PTT)",
    TRIGGER_BUTTON_1: "Header Button 1",
    TRIGGER_BUTTON_2: "Header Button 2",
    TRIGGER_TEMP: "Temperature Threshold",
}


# ─── Data Models ───────────────────────────────────────────────────

@dataclass
class PinConfig:
    """Configuration for a single GPIO pin.

    Maps directly to the 5-column spec:
      Col 1: bcm_pin
      Col 2: logic_level, output_mode
      Col 3: trigger
      Col 4: button_label, sequencer_delay_ms, ptt_combo_button, band, temp_on, temp_off
      Col 5: session_bound
    """
    # Column 1: GPIO ID
    bcm_pin: int

    # Column 2: Electrical characteristic
    logic_level: str = "active_high"     # "active_high" | "active_low"
    output_mode: str = "push_pull"       # "push_pull" | "open_drain" (push_pull implemented)

    # Column 3: Trigger
    trigger: str = ""                    # One of TRIGGER_* or "" (unused)

    # Column 4: Parameters (trigger-dependent)
    button_label: str = ""               # For button1/button2 triggers
    sequencer_delay_ms: int = 0          # For PTT: delay before activating (PA protection)
    ptt_combo_button: str = ""           # "" | "button1" | "button2" – AND condition
    band: str = ""                       # For band decoder: "2m", "70cm", etc.
    temp_on: float = 0.0                 # Temperature threshold: activate above
    temp_off: float = 0.0                # Temperature threshold: deactivate below

    # Column 5: Fail-Safe & Session safety
    session_bound: bool = False          # Pin OFF when no user session active

    # DS18B20 / Input support
    direction: str = "output"            # "output" | "input"
    input_type: str = ""                 # "ds18b20" | ""
    sensor_name: str = ""                # Name for DS18B20 sensor (e.g. "PA-Temp")
    sensor_id: str = ""                  # Auto-detected 1-Wire ID (e.g. "28-00000xxxx")
    show_temp: bool = False              # If True, temperature shown on main page
    temp_source: str = ""                # For temp trigger: which sensor_name to watch

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PinConfig":
        """Create from dict, ignoring unknown keys."""
        valid = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**valid)


class PinHandle:
    """Wraps a gpiozero OutputDevice with config and logical-state tracking."""

    def __init__(self, config: PinConfig):
        self.config = config
        self._device: Any = None
        self._active = False

    def init_hardware(self):
        """Initialize gpiozero device. Imported lazily so non-Pi hosts don't crash."""
        from gpiozero import OutputDevice
        active_high = (self.config.logic_level == "active_high")
        self._device = OutputDevice(
            self.config.bcm_pin,
            active_high=active_high,
            initial_value=False,  # Always start OFF (safe state)
        )
        self._active = False

    def on(self):
        """Activate pin."""
        if self._device:
            try:
                self._device.on()
                self._active = True
                logger.debug(f"GPIO {self.config.bcm_pin} -> ON")
            except Exception as e:
                logger.error(f"GPIO {self.config.bcm_pin} ON failed: {e}")

    def off(self):
        """Deactivate pin (safe state)."""
        if self._device:
            try:
                self._device.off()
                self._active = False
                logger.debug(f"GPIO {self.config.bcm_pin} -> OFF")
            except Exception as e:
                logger.error(f"GPIO {self.config.bcm_pin} OFF failed: {e}")

    @property
    def is_active(self) -> bool:
        return self._active

    def close(self):
        """Release hardware resource."""
        if self._device:
            try:
                self._device.close()
            except Exception:
                pass
            self._device = None
        self._active = False


# ─── Manager ───────────────────────────────────────────────────────

class GPIOManager:
    """Singleton managing all GPIO pins, triggers, and safety logic."""

    def __init__(self):
        self._handles: dict[int, PinHandle] = {}
        self._configs: list[PinConfig] = []
        self._button_states: dict[str, bool] = {"button1": False, "button2": False}
        self._ptt_active = False
        self._ptt_generation = 0
        self._ptt_apply_task: asyncio.Task | None = None
        self._active_users: set[str] = set()
        self._initialized = False
        self._temp_task = None

    # ─── Config ─────────────────────────────────────

    def load_configs(self, pin_dicts: list[dict]):
        """Parse and store configs from list of dicts."""
        self._configs = [PinConfig.from_dict(d) for d in pin_dicts]

    def reload_from_config(self):
        """Reload pin configs from config.yaml + config.local.yaml."""
        from backend.config import get
        pin_dicts = get("gpio.pins", [])
        self._configs = [PinConfig.from_dict(d) for d in pin_dicts]

    def get_configs(self) -> list[dict]:
        """Return current configs as list of dicts."""
        return [c.to_dict() for c in self._configs]

    def get_available_pins(self) -> list[int]:
        """Return BCM pins available for output assignment (not reserved, not configured)."""
        configured = {c.bcm_pin for c in self._configs}
        return [p for p in OUTPUT_PINS if p not in configured]

    # ─── Lifecycle ──────────────────────────────────

    async def initialize(self):
        """Initialize all configured pins. Fail-safe: all start OFF."""
        self.cleanup()
        self.reload_from_config()

        for cfg in self._configs:
            if cfg.bcm_pin in HARD_RESERVED:
                logger.warning(f"Pin {cfg.bcm_pin} is hard-reserved, skipping")
                continue

            # Input pins (e.g. DS18B20 on 1-Wire) are sysfs-only — never gpiozero
            if cfg.direction == "input":
                if cfg.input_type == "ds18b20":
                    logger.info(
                        f"GPIO {cfg.bcm_pin} input: DS18B20 "
                        f"'{cfg.sensor_name or '(unnamed)'}' (1-Wire, no gpiozero)"
                    )
                else:
                    logger.info(f"GPIO {cfg.bcm_pin} input: config only, no gpiozero")
                continue

            if cfg.bcm_pin in ALL_RESERVED:
                logger.warning(f"Pin {cfg.bcm_pin} is soft-reserved, skipping for output")
                continue

            try:
                handle = PinHandle(cfg)
                handle.init_hardware()
                self._handles[cfg.bcm_pin] = handle
                logger.info(
                    f"GPIO {cfg.bcm_pin} output init: "
                    f"{cfg.logic_level}, trigger={cfg.trigger}, "
                    f"session_bound={cfg.session_bound}"
                )
            except Exception as e:
                logger.error(f"GPIO {cfg.bcm_pin} init failed: {e}")

        self._initialized = True
        # Ensure all OFF (redundant, but explicit = safe)
        self.all_off()
        # Start temperature monitor for temp-trigger pins
        self._start_temp_monitor()
        output_count = len(self._handles)
        sensor_count = sum(
            1 for c in self._configs
            if c.direction == "input" and c.input_type == "ds18b20"
        )
        logger.info(
            f"GPIO manager initialized ({output_count} output pin"
            f"{'' if output_count == 1 else 's'}, {sensor_count} 1-Wire sensor"
            f"{'' if sensor_count == 1 else 's'})"
        )


    def _start_temp_monitor(self):
        """Start (or restart) the temperature monitor background task."""
        self._stop_temp_monitor()
        # Only start if there are temp-trigger pins
        has_temp = any(
            c.trigger == TRIGGER_TEMP
            for c in self._configs
        )
        if has_temp:
            self._temp_task = asyncio.create_task(self._temp_monitor_loop())
            logger.info("Temperature monitor task started (5s interval)")
        else:
            logger.debug("No temp-trigger pins configured, temp monitor not started")

    def _stop_temp_monitor(self):
        """Stop the temperature monitor background task if running."""
        if self._temp_task and not self._temp_task.done():
            self._temp_task.cancel()
            logger.info("Temperature monitor task stopped")
        self._temp_task = None

    async def _temp_monitor_loop(self):
        """Background loop: evaluate temp-trigger pins every 5 seconds."""
        logger.info("Temperature monitor loop running")
        while True:
            try:
                await asyncio.sleep(5)
                self._evaluate_temp_triggers()
            except asyncio.CancelledError:
                logger.info("Temperature monitor loop cancelled")
                break
            except Exception as e:
                logger.error(f"Temperature monitor error: {e}", exc_info=True)

    def _read_sensor_temp(self, sensor_id, w1_base):
        """Read temperature from a DS18B20 sensor. Returns float or None."""
        w1_file = w1_base / sensor_id / "w1_slave"
        if not w1_file.exists():
            return None
        raw = w1_file.read_text().strip()
        lines = raw.split("\n")
        if lines and "YES" not in lines[0]:
            return None
        m = re.search(r"t=(-?\d+)", raw)
        if m:
            return round(int(m.group(1)) / 1000.0, 1)
        return None

    def _ds18b20_config_by_name(self) -> dict[str, PinConfig]:
        """Map sensor_name -> input config for all DS18B20 rows."""
        by_name: dict[str, PinConfig] = {}
        for cfg in self._configs:
            if cfg.input_type == "ds18b20" and cfg.sensor_name:
                by_name[cfg.sensor_name] = cfg
        return by_name

    def _ds18b20_sensor_names_needed(self) -> set[str]:
        """Sensor names to read: UI display and/or temp-trigger sources."""
        names: set[str] = set()
        for cfg in self._configs:
            if cfg.input_type == "ds18b20" and cfg.sensor_name:
                if cfg.show_temp:
                    names.add(cfg.sensor_name)
        for cfg in self._configs:
            if cfg.trigger == TRIGGER_TEMP and cfg.temp_source:
                names.add(cfg.temp_source)
        return names

    def _resolve_ds18b20_sensor_id(self, cfg: PinConfig | None, w1_base: Path) -> str | None:
        """Resolve 1-Wire device id from config or auto-detect first DS18B20."""
        sensor_id = cfg.sensor_id if cfg else ""
        if sensor_id:
            return sensor_id
        if not w1_base.exists():
            return None
        for dev_dir in w1_base.iterdir():
            if dev_dir.name.startswith("28-"):
                return dev_dir.name
        return None

    def _build_temp_cache(self) -> dict[str, float | None]:
        """Read temperatures for all sensors needed by UI and temp triggers."""
        w1_base = Path("/sys/bus/w1/devices")
        sensors_by_name = self._ds18b20_config_by_name()
        temp_cache: dict[str, float | None] = {}

        for name in self._ds18b20_sensor_names_needed():
            cfg = sensors_by_name.get(name)
            sensor_id = self._resolve_ds18b20_sensor_id(cfg, w1_base)
            if not sensor_id:
                temp_cache[name] = None
                continue
            try:
                temp_cache[name] = self._read_sensor_temp(sensor_id, w1_base)
            except Exception as e:
                logger.warning(f"Temp read error for {name}: {e}")
                temp_cache[name] = None

        return temp_cache

    def _evaluate_temp_triggers(self):
        """Check all temp-trigger pins against their sensor thresholds."""
        temp_cache = self._build_temp_cache()

        # Evaluate each temp-trigger pin
        for handle in self._handles.values():
            cfg = handle.config
            if cfg.trigger != TRIGGER_TEMP:
                continue

            source_name = cfg.temp_source
            if not source_name:
                continue

            if source_name not in temp_cache:
                logger.warning(
                    f"Temp pin {cfg.bcm_pin}: source '{source_name}' "
                    f"has no DS18B20 input configured"
                )
                continue

            temp = temp_cache[source_name]
            if temp is None:
                logger.debug(
                    f"Temp pin {cfg.bcm_pin}: source '{source_name}' "
                    f"not readable, holding state"
                )
                continue

            if temp >= cfg.temp_on:
                if not handle.is_active:
                    logger.info(
                        f"Temp pin {cfg.bcm_pin} -> ON "
                        f"({source_name}={temp}C >= {cfg.temp_on}C)"
                    )
                    handle.on()
            elif temp <= cfg.temp_off:
                if handle.is_active:
                    logger.info(
                        f"Temp pin {cfg.bcm_pin} -> OFF "
                        f"({source_name}={temp}C <= {cfg.temp_off}C)"
                    )
                    handle.off()
            # Between thresholds: hysteresis, hold current state

    def cleanup(self):
        """Release all pins. Fail-safe: everything OFF first, then release."""
        self._stop_temp_monitor()
        self.all_off()
        for handle in self._handles.values():
            handle.close()
        self._handles.clear()
        self._initialized = False
        logger.info("GPIO manager cleaned up")

    def all_off(self):
        """Emergency stop: all pins to safe inactive state."""
        for handle in self._handles.values():
            handle.off()

    # ─── Trigger Handlers ───────────────────────────

    def _ptt_pin_allowed(self, cfg: PinConfig) -> bool:
        """Return True if a PTT-triggered pin may be activated right now."""
        if cfg.session_bound and not self._active_users:
            return False
        if cfg.ptt_combo_button:
            if not self._button_states.get(cfg.ptt_combo_button, False):
                return False
        return True

    def _set_all_ptt_pins(self, active: bool):
        """Turn all PTT-triggered output pins on or off."""
        for handle in self._handles.values():
            if handle.config.trigger != TRIGGER_PTT:
                continue
            if active and self._ptt_pin_allowed(handle.config):
                handle.on()
            else:
                handle.off()

    async def _cancel_ptt_apply_task(self):
        """Cancel any in-flight PTT sequencer work."""
        task = self._ptt_apply_task
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._ptt_apply_task = None

    async def _ptt_delayed_on(self, handle: PinHandle, gen: int):
        """Activate one PTT pin after its sequencer delay, if still valid."""
        delay_s = handle.config.sequencer_delay_ms / 1000.0
        try:
            await asyncio.sleep(delay_s)
        except asyncio.CancelledError:
            raise
        if gen != self._ptt_generation or not self._ptt_active:
            return
        if not self._ptt_pin_allowed(handle.config):
            handle.off()
            return
        handle.on()

    async def _apply_ptt_on(self, gen: int):
        """Apply PTT-on to eligible pins (immediate + delayed in parallel)."""
        try:
            ptt_handles = [
                h for h in self._handles.values()
                if h.config.trigger == TRIGGER_PTT and self._ptt_pin_allowed(h.config)
            ]
            for handle in ptt_handles:
                if handle.config.sequencer_delay_ms <= 0:
                    if gen != self._ptt_generation or not self._ptt_active:
                        return
                    handle.on()

            delayed = [h for h in ptt_handles if h.config.sequencer_delay_ms > 0]
            if delayed:
                await asyncio.gather(
                    *(self._ptt_delayed_on(h, gen) for h in delayed)
                )
        except asyncio.CancelledError:
            raise
        finally:
            if gen == self._ptt_generation:
                self._ptt_apply_task = None

    async def on_ptt(self, active: bool):
        """Handle PTT trigger with sequencer delay and optional combo condition.

        If a pin has ptt_combo_button set, the pin only activates when
        both PTT is active AND the specified button is armed.
        Pending sequencer delays are cancelled when PTT releases.
        """
        self._ptt_generation += 1
        gen = self._ptt_generation
        self._ptt_active = active

        await self._cancel_ptt_apply_task()

        if not active:
            self._set_all_ptt_pins(False)
            return

        for handle in self._handles.values():
            if handle.config.trigger != TRIGGER_PTT:
                continue
            if not self._ptt_pin_allowed(handle.config):
                handle.off()

        self._ptt_apply_task = asyncio.create_task(self._apply_ptt_on(gen))

    async def on_button_toggle(self, button_id: str, active: bool):
        """Handle header button press (button1 or button2)."""
        if button_id not in ("button1", "button2"):
            logger.warning(f"Unknown button_id: {button_id}")
            return

        self._button_states[button_id] = active
        logger.info(f"GPIO button {button_id} -> {'ON' if active else 'OFF'}")

        # Direct-triggered pins
        for handle in self._handles.values():
            cfg = handle.config
            if cfg.trigger != button_id:
                continue

            if cfg.session_bound and not self._active_users:
                handle.off()
                continue

            if active:
                handle.on()
            else:
                handle.off()

        # Re-evaluate PTT-combo pins (button state change may affect them)
        if self._ptt_active:
            asyncio.create_task(self._refresh_ptt_combos())

    async def _refresh_ptt_combos(self):
        """Re-evaluate PTT pins with combo conditions after button state change."""
        await self.on_ptt(self._ptt_active)

    # ─── Session Tracking ───────────────────────────

    def _turn_off_session_bound_pins(self):
        """Turn off all session-bound pins (safe state)."""
        count = 0
        for handle in self._handles.values():
            if handle.config.session_bound:
                handle.off()
                count += 1
        if count:
            logger.info(f"GPIO: {count} session-bound pin(s) -> OFF (no active sessions)")

    def on_session_login(self, user: str):
        """Called when a user logs in or reconnects after watchdog."""
        if not user:
            return
        self._active_users.add(user)
        logger.debug(f"GPIO: session login user={user} active_users={sorted(self._active_users)}")

    def on_session_logout(self, user: str | None):
        """Called when a user session ends (logout, tab close, timeout, watchdog)."""
        if user:
            self._active_users.discard(user)
        logger.debug(f"GPIO: session logout user={user} active_users={sorted(self._active_users)}")

        if not self._active_users:
            self._turn_off_session_bound_pins()

    # ─── Status & Info ──────────────────────────────

    def get_status(self) -> list[dict]:
        """Get live status of all configured pins."""
        result = []
        for handle in self._handles.values():
            cfg = handle.config
            result.append({
                "pin": cfg.bcm_pin,
                "trigger": cfg.trigger,
                "trigger_label": TRIGGER_LABELS.get(cfg.trigger, cfg.trigger),
                "active": handle.is_active,
                "session_bound": cfg.session_bound,
                "logic_level": cfg.logic_level,
                "button_label": cfg.button_label if cfg.trigger in ("button1", "button2") else "",
            })
        return result

    def get_button_info(self) -> dict:
        """Return header button labels and active states for frontend top-bar."""
        info = {}
        for cfg in self._configs:
            if cfg.trigger == TRIGGER_BUTTON_1 and cfg.button_label:
                info["button1"] = {
                    "label": cfg.button_label,
                    "active": self._button_states["button1"],
                    "session_bound": cfg.session_bound,
                }
            elif cfg.trigger == TRIGGER_BUTTON_2 and cfg.button_label:
                info["button2"] = {
                    "label": cfg.button_label,
                    "active": self._button_states["button2"],
                    "session_bound": cfg.session_bound,
                }
        return info

    # --- Temperature Sensors (DS18B20) ---

    def get_temperatures(self) -> list[dict]:
        """Read all configured DS18B20 sensors and return their current values.

        Returns a list of dicts: [{name, id, temp, pin, error?}, ...]
        Always returns an entry for every sensor with show_temp=True,
        even if the temperature cannot be read (temp=null, error=reason).
        Reads from /sys/bus/w1/devices/<sensor_id>/w1_slave (standard 1-Wire).
        """
        results = []
        w1_base = Path("/sys/bus/w1/devices")

        for cfg in self._configs:
            # Return entry for ALL ds18b20 sensors with show_temp=True,
            # regardless of whether physical sensor is detected.
            if cfg.input_type != "ds18b20" or not cfg.show_temp:
                continue

            entry = {
                "name": cfg.sensor_name or f"Sensor-{cfg.bcm_pin}",
                "id": cfg.sensor_id or "",
                "pin": cfg.bcm_pin,
                "temp": None,
            }

            # Check if sensor_id is configured
            sensor_id = cfg.sensor_id

            # Auto-detect if not explicitly set
            if not sensor_id:
                if not w1_base.exists():
                    entry["error"] = "1-Wire not enabled (/sys/bus/w1 not found)"
                    results.append(entry)
                    continue
                # Try auto-detect
                for dev_dir in w1_base.iterdir():
                    if dev_dir.name.startswith("28-"):
                        sensor_id = dev_dir.name
                        break
                if not sensor_id:
                    entry["error"] = "No DS18B20 sensor found (auto-detect)"
                    results.append(entry)
                    continue

            # Now we have a sensor_id — check if the device file exists
            entry["id"] = sensor_id
            w1_file = w1_base / sensor_id / "w1_slave"

            if not w1_file.exists():
                entry["error"] = f"Sensor not connected (ID: {sensor_id})"
                results.append(entry)
                continue

            try:
                raw = w1_file.read_text().strip()
                lines = raw.split("\n")

                if lines and "YES" not in lines[0]:
                    entry["error"] = "CRC check failed"
                    results.append(entry)
                    continue

                m = re.search(r"t=(-?\d+)", raw)
                if m:
                    temp_raw = int(m.group(1))
                    temp_c = round(temp_raw / 1000.0, 1)
                    entry["temp"] = temp_c
                else:
                    entry["error"] = "Could not read temperature"

            except Exception as e:
                entry["error"] = f"Lesefehler: {e}"

            results.append(entry)

        return results

    @property
    def initialized(self) -> bool:
        return self._initialized


# Singleton instance
manager = GPIOManager()
