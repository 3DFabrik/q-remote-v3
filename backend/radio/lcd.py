"""
LCD display - pixel-positioned text rendering matching QuanshengDock.
Ported from V1's lcd.py - processes type 5/6 UI packets into fragment state.
"""
import logging
import time

log = logging.getLogger(__name__)

LCD_LINES = 8


class LCDDisplay:
    def __init__(self):
        self.fragments = {i: [] for i in range(LCD_LINES)}
        self.smeter = 0
        self.rssi = -120
        self.state = 'idle'
        self.battery_v = 0.0
        self.battery_pct = 0
        self.indicators = {}
        self._last_state = None
        self._last_push = 0.0
        self.active_vfo_line = None
        self._change_callbacks = []

    def on_change(self, callback):
        self._change_callbacks.append(callback)

    def flush(self):
        now = time.time()
        delta = now - self._last_push
        if delta < 0.1:
            return
        log.info(f"LCD flush: delta={delta:.3f}s, callbacks={len(self._change_callbacks)}")
        self._last_push = now
        state = self.get_state()
        # Always push for now (debug)
        self._last_state = state
        for cb in self._change_callbacks:
            try:
                cb(state)
            except Exception as e:
                log.error(f"LCD callback error: {e}")

    def process_ui_packet(self, ui_type, val1, val2, val3, data_len, data):
        log.info(f"LCD process: type={ui_type} v1={val1} v2={val2} v3={val3} dlen={data_len}")
        if ui_type == 0:
            text = data.decode('ascii', errors='replace') if data else ""
            y = val2 + 1
            x = val1
            while x > 128: y += 1; x -= 128
            self._add_fragment(x, y, 1.5, text, False, False)
        elif ui_type == 1:
            text = data.decode('ascii', errors='replace') if data else ""
            y = val2 + 1
            x = val1
            while x > 128: y += 1; x -= 128
            self._add_fragment(x, y, val3 / 6.0, text, False, False)
        elif ui_type == 2:
            text = data.decode('ascii', errors='replace') if data else ""
            y = val2 + 1
            x = val1
            while x > 128: y += 1; x -= 128
            self._add_fragment(x, y, val3 / 6.0, text, True, True)
        elif ui_type == 3:
            text = data.decode('ascii', errors='replace') if data else ""
            y = val2 + 1
            x = val1
            while x > 128: y += 1; x -= 128
            self._add_fragment(x, y, 2.0, text, False, True)
        elif ui_type == 5:
            for i in range(val1, val2 + 1):
                if i > 0 and i < LCD_LINES:
                    self.fragments[i] = []
        elif ui_type == 6:
            self._process_status(val1, val2, val3, data_len)
        elif ui_type == 7:
            y = val1
            marker = '▻' if val2 == 0 else '▶'
            self._add_fragment(0, y, 1.0, marker, False, False)
            if val2 != 0:
                self.active_vfo_line = y
        elif ui_type == 8:
            self.smeter = val1

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
            'smeter': self.smeter,
            'state': self.state,
            'tx': self.state == 'TX',
            'rx': self.state == 'RX',
            'battery_v': self.battery_v,
            'battery_pct': self.battery_pct,
            'indicators': dict(self.indicators),
        }

    def clear(self):
        self.fragments = {i: [] for i in range(LCD_LINES)}
        self.smeter = 0
