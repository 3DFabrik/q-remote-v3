"""
LCD display - pixel-positioned text rendering matching QuanshengDock.
Ported from V1's lcd.py - processes type 5/6 UI packets into fragment state.
"""
import logging
import re
import time
from typing import Optional

log = logging.getLogger(__name__)

LCD_LINES = 8
_FREQ_RE = re.compile(r"(\d{1,3}\.\d{4,5})")
_RSSI_LINES = (3, 4)
_RSSI_TEXT_RE = re.compile(r"^-?\d+")
_VFO_MARKERS = ("▶", "▻", "➤", "▸")
# Remote UI: upper VFO block lines 1–2, lower block lines 4–5 (type-7 triangle on 1 or 4).
_VFO_UPPER_LINES = (1, 2)
_VFO_LOWER_LINES = (4, 5)


class LCDDisplay:
    def __init__(self):
        self.fragments = {i: [] for i in range(LCD_LINES)}
        self.smeter = 0
        self.rssi = -120
        self._rssi_history = []
        self._last_rssi_time = 0.0
        self.state = 'idle'
        self.battery_v = 0.0
        self.battery_pct = 0
        self.indicators = {}
        self._last_state = None
        self._last_push = 0.0
        self.active_vfo_line: Optional[int] = None
        self._vfo_markers: dict[int, bool] = {}
        self._vfo_frequencies: dict[int, float] = {}
        self._change_callbacks = []

    def on_change(self, callback):
        self._change_callbacks.append(callback)

    def flush(self):
        now = time.time()
        if now - self._last_push < 0.1:
            return
        self._last_push = now
        state = self.get_state()
        if self._last_state is not None and state == self._last_state:
            return
        self._last_state = state
        for cb in self._change_callbacks:
            try:
                cb(state)
            except Exception as e:
                log.error(f"LCD callback error: {e}")

    def force_flush(self):
        """Force push current state to clients, ignoring throttle."""
        state = self.get_state()
        self._last_state = state
        for cb in self._change_callbacks:
            try:
                cb(state)
            except Exception as e:
                log.error(f"LCD callback error: {e}")
        state = self.get_state()
        if self._last_state is not None and state == self._last_state:
            return
        self._last_state = state
        for cb in self._change_callbacks:
            try:
                cb(state)
            except Exception as e:
                log.error(f"LCD callback error: {e}")

    def process_ui_packet(self, ui_type, val1, val2, val3, data_len, data):
        if ui_type in (0, 1, 2, 3) and data:
            log.debug(
                "UI text type=%s x=%s y=%s sz=%s text='%s'",
                ui_type, val1, val2, val3,
                data.decode('ascii', errors='replace'),
            )
        if ui_type == 0:
            text = data.decode('ascii', errors='replace') if data else ""
            y = val2 + 1
            x = val1
            while x > 128: y += 1; x -= 128
            self._add_fragment(x, y, 1.5, text, False, False)
            self._store_line_frequency(y, text)
        elif ui_type == 1:
            text = data.decode('ascii', errors='replace') if data else ""
            y = val2 + 1
            x = val1
            while x > 128: y += 1; x -= 128
            self._add_fragment(x, y, val3 / 6.0, text, False, False)
            self._store_line_frequency(y, text)
            if y == 3 or y == 4:
                self._parse_rssi_text(text)
        elif ui_type == 2:
            text = data.decode('ascii', errors='replace') if data else ""
            y = val2 + 1
            x = val1
            while x > 128: y += 1; x -= 128
            self._add_fragment(x, y, val3 / 6.0, text, True, True)
            self._store_line_frequency(y, text)
        elif ui_type == 3:
            text = data.decode('ascii', errors='replace') if data else ""
            y = val2 + 1
            x = val1
            while x > 128: y += 1; x -= 128
            self._add_fragment(x, y, 2.0, text, False, True)
            self._store_line_frequency(y, text)
        elif ui_type == 5:
            for i in range(val1, val2 + 1):
                if i > 0 and i < LCD_LINES:
                    self.fragments[i] = []
                    self._vfo_markers.pop(i, None)
                    self._vfo_frequencies.pop(i, None)
            self._refresh_active_vfo_line()
        elif ui_type == 6:
            self._process_status(val1, val2, val3, data_len)
        elif ui_type == 7:
            y = val1
            filled = val2 != 0
            self._vfo_markers[y] = filled
            marker = "▶" if filled else "▻"
            self._add_fragment(0, y, 1.0, marker, False, False)
            self._refresh_active_vfo_line()
            log.debug("VFO marker line=%d filled=%s active=%s", y, filled, self.active_vfo_line)
        elif ui_type == 8:
            log.info(f"S-Meter type 8: val1={val1} val2={val2} val3={val3}")
            self.smeter = val1

    def _parse_rssi_text(self, text):
        """Extract dBm from radio display text.
        
        Formats:
          Normal:  '-107 S3'   → dBm + S-unit
          Over S9: '-36  40'   → dBm + dB over S9
        """
        try:
            text = text.strip()
            parts = text.split()
            if not parts:
                return
            dbm = int(parts[0])
            self._last_rssi_time = time.time()
            # Instant value – display text is already stable
            self.rssi = dbm
            if len(parts) >= 2:
                s_part = parts[1]
                if s_part.startswith('S'):
                    log.info(f"RSSI from display: dbm={dbm} {s_part}")
                else:
                    log.info(f"RSSI from display: dbm={dbm} S9+{s_part}dB")
            else:
                log.info(f"RSSI from display: dbm={dbm}")
        except (ValueError, IndexError):
            pass

    def check_rssi_timeout(self) -> bool:
        """Reset RSSI if no new display text; clear stale RSSI fragments on CRT."""
        if self._last_rssi_time > 0 and time.time() - self._last_rssi_time > 0.5:
            self.rssi = -120
            self._last_rssi_time = 0
            self._clear_rssi_fragments()
            log.info("RSSI: no display text, resetting to -120")
            return True
        return False

    def _clear_rssi_fragments(self) -> None:
        for line in _RSSI_LINES:
            self.fragments[line] = [
                f for f in self.fragments[line]
                if not _RSSI_TEXT_RE.match(f.get("text", "").strip())
            ]

    def _add_fragment(self, x, y, size, text, inverted, bold):
        if y < 0 or y >= LCD_LINES:
            return
        self.fragments[y] = [f for f in self.fragments[y] if f['x'] != x]
        if text:
            self.fragments[y].append({
                'x': x, 'text': text, 'size': size,
                'inverted': inverted, 'bold': bold
            })
            self.fragments[y].sort(key=lambda f: f['x'])

    def _process_status(self, val1, val2, val3, data_len):
        parts = []
        state_val = val1 & 7
        if state_val == 1:
            parts.append('T')
            self.state = 'TX'
        elif state_val == 2:
            parts.append('R')
            self.state = 'RX'
        elif state_val == 4:
            parts.append('PS')
            self.state = 'PS'
        else:
            self.state = 'idle'
        if val1 & 8: parts.append('NOA')
        if val1 & 16: parts.append('DTMF')
        if val1 & 32: parts.append('FM')
        if val3 != 0 and val3 < 128: parts.append(chr(val3))
        if val1 & 64: parts.append('◀')
        if val1 & 128: parts.append('DWR')
        if val2 & 1: parts.append('><')
        if val2 & 2: parts.append('XB')
        if val2 & 4: parts.append('VOX')
        if val2 & 8: parts.append('🔒')
        bat = data_len * 0.04
        if bat > 8.4: bat = 8.4
        pct = round(data_len / 2.1)
        self.battery_v = round(bat, 2)
        self.battery_pct = min(100, pct)
        parts.append(f'{self.battery_v}V {self.battery_pct}%')
        status_text = ' '.join(parts)
        self.fragments[0] = [{
            'x': 0, 'text': status_text, 'size': 0.5,
            'inverted': False, 'bold': False
        }]
        self.indicators = {
            'fm': (val1 & 32) != 0,
            'dwr': (val1 & 128) != 0,
            'vox': (val2 & 4) != 0,
            'lock': (val2 & 8) != 0,
        }

    def get_state(self):
        return {
            'fragments': {
                str(y): self.fragments[y] for y in range(LCD_LINES)
            },
            'rssi_dbm': self.rssi,
            'smeter': self.smeter,
            'state': self.state,
            'tx': self.state == 'TX',
            'rx': self.state == 'RX',
            'battery_v': self.battery_v,
            'battery_pct': self.battery_pct,
            'indicators': dict(self.indicators),
            'active_vfo_line': self.active_vfo_line,
        }

    def _refresh_active_vfo_line(self) -> None:
        filled = [line for line, is_filled in self._vfo_markers.items() if is_filled]
        if len(filled) == 1:
            self.active_vfo_line = filled[0]
        elif not filled:
            self.active_vfo_line = None
        else:
            self.active_vfo_line = min(filled)

    @staticmethod
    def _parse_freq_text(text: str) -> Optional[float]:
        if not text or text in _VFO_MARKERS:
            return None
        match = _FREQ_RE.search(text)
        if not match:
            return None
        try:
            value = float(match.group(1))
        except ValueError:
            return None
        if 100.0 <= value <= 999.99999:
            return value
        return None

    def _store_line_frequency(self, line: int, text: str) -> None:
        freq = self._parse_freq_text(text)
        if freq is not None:
            self._vfo_frequencies[line] = freq

    def _vfo_block_lines(self, anchor: Optional[int]) -> tuple[int, ...]:
        if anchor is None:
            return _VFO_UPPER_LINES + _VFO_LOWER_LINES
        if anchor in _VFO_UPPER_LINES or anchor < 3:
            return _VFO_UPPER_LINES
        return _VFO_LOWER_LINES

    def _frequency_on_line(self, line: int) -> Optional[float]:
        if line in self._vfo_frequencies:
            return self._vfo_frequencies[line]
        for frag in self.fragments.get(line, []):
            freq = self._parse_freq_text(frag.get("text", ""))
            if freq is not None:
                self._vfo_frequencies[line] = freq
                return freq
        return None

    def parse_active_vfo_frequency_mhz(self) -> Optional[float]:
        """Frequency of the VFO marked by the filled triangle (UI type 7)."""
        lines = self._vfo_block_lines(self.active_vfo_line)
        if self.active_vfo_line is not None:
            ordered = (self.active_vfo_line,) + tuple(
                ln for ln in lines if ln != self.active_vfo_line
            )
        else:
            ordered = lines
        for line in ordered:
            freq = self._frequency_on_line(line)
            if freq is not None:
                return freq
        return self.parse_frequency_mhz()

    def parse_frequency_mhz(self) -> Optional[float]:
        """First frequency found on the display (fallback)."""
        for line in range(LCD_LINES):
            freq = self._frequency_on_line(line)
            if freq is not None:
                return freq
        return None

    def active_vfo_label(self) -> str:
        if self.active_vfo_line is None:
            return "?"
        if self.active_vfo_line in _VFO_UPPER_LINES:
            return "A"
        if self.active_vfo_line in _VFO_LOWER_LINES:
            return "B"
        return str(self.active_vfo_line)

    def clear(self):
        self.fragments = {i: [] for i in range(LCD_LINES)}
        self.smeter = 0
        self._vfo_markers.clear()
        self._vfo_frequencies.clear()
        self.active_vfo_line = None

