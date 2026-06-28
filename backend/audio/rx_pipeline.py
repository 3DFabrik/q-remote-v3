"""RX Audio Pipeline: ALSA capture -> ulaw encode -> WebSocket clients.

Captures 8kHz 16-bit PCM from AIOC, encodes to G.711 ulaw,
sends to WebSocket clients. 20ms chunks for low latency.
No audioop dependency - uses builtin ulaw table.
"""

import asyncio
import logging
import math
import struct
import subprocess
import threading
import time
from typing import Set

log = logging.getLogger(__name__)

SAMPLE_RATE = 8000
CHANNELS = 1
SAMPLE_WIDTH = 2
CHUNK_SAMPLES = 160  # 20ms
CHUNK_BYTES_PCM = CHUNK_SAMPLES * SAMPLE_WIDTH  # 320 bytes

AIOC_DEVICE = "hw:CARD=AllInOneCable,DEV=0"


def _build_ulaw_table():
    """Build 65536-entry lookup: signed 16-bit -> ulaw byte."""
    BIAS = 0x84
    CLIP = 32635
    table = bytearray(65536)
    for i in range(65536):
        sample = i if i < 32768 else i - 65536
        if sample > CLIP:
            sample = CLIP
        elif sample < -CLIP:
            sample = -CLIP
        sign = 0x80 if sample < 0 else 0x00
        if sign:
            sample = -sample
        sample += BIAS
        if sample >= 0x4000:    exp = 7
        elif sample >= 0x2000:  exp = 6
        elif sample >= 0x1000:  exp = 5
        elif sample >= 0x0800:  exp = 4
        elif sample >= 0x0400:  exp = 3
        elif sample >= 0x0200:  exp = 2
        elif sample >= 0x0100:  exp = 1
        else:                   exp = 0
        mantissa = (sample >> (exp + 3)) & 0x0F
        table[i] = ~(sign | (exp << 4) | mantissa) & 0xFF
    return bytes(table)


_ULAW_TABLE = _build_ulaw_table()


def pcm_to_ulaw(pcm_data: bytes) -> bytes:
    """Convert 16-bit signed PCM to ulaw using lookup table."""
    import array
    n = len(pcm_data) // 2
    samples = array.array("h", pcm_data[:n * 2])
    result = bytearray(n)
    for i in range(n):
        idx = (samples[i] + 65536) & 0xFFFF if samples[i] < 0 else samples[i]
        result[i] = _ULAW_TABLE[idx]
    return bytes(result)


def _apply_gain_ramp_pcm(pcm_data: bytes, start_gain: float, end_gain: float) -> bytes:
    """Linear gain ramp across one chunk — softens squelch open/close clicks."""
    import array

    if start_gain >= 0.999 and end_gain >= 0.999:
        return pcm_data
    samples = array.array("h", pcm_data)
    n = len(samples)
    if n == 0:
        return pcm_data
    if abs(start_gain - end_gain) < 1e-6:
        g = start_gain
        if g >= 0.999:
            return pcm_data
        for i in range(n):
            samples[i] = int(max(-32768, min(32767, samples[i] * g)))
        return samples.tobytes()

    denom = max(1, n - 1)
    for i in range(n):
        g = start_gain + (end_gain - start_gain) * (i / denom)
        samples[i] = int(max(-32768, min(32767, samples[i] * g)))
    return samples.tobytes()


class RxPipeline:
    def __init__(self):
        self._process = None
        self._running = False
        self._thread = None
        self._clients: Set = set()
        self._loop = None
        # Noise gate (squelch) — opens on audio RMS and/or S-meter signal (RSSI)
        self.squelch_enabled = True
        self.squelch_threshold = 300  # RMS threshold (0-32768), ~-40dB
        self.signal_threshold_dbm = -115  # RSSI above this opens gate (S-meter)
        self.signal_stale_s = 0.5  # RSSI considered stale after this silence
        self.gate_hold_ms = 200  # keep gate open after signal/audio drops
        self.gate_attack_ms = 35  # fade-in when opening (reduces punch/click)
        self.gate_release_ms = 25  # fade-out when closing
        self._signal_lock = threading.Lock()
        self._signal_dbm = -120
        self._signal_updated_at = 0.0
        self._gate_gain = 0.0
        self._gate_hold_frames = 0

    def add_client(self, websocket):
        self._clients.add(websocket)
        log.info(f"RX audio client added ({len(self._clients)} total)")

    def remove_client(self, websocket):
        self._clients.discard(websocket)
        log.info(f"RX audio client removed ({len(self._clients)} total)")

    def update_signal_dbm(self, dbm: int) -> None:
        """Feed RSSI from radio LCD (same source as S-meter). Thread-safe."""
        with self._signal_lock:
            self._signal_dbm = int(dbm)
            self._signal_updated_at = time.monotonic()

    def _signal_present(self) -> bool:
        with self._signal_lock:
            if self._signal_updated_at <= 0:
                return False
            if time.monotonic() - self._signal_updated_at > self.signal_stale_s:
                return False
            return self._signal_dbm > self.signal_threshold_dbm

    def _gate_hold_count(self) -> int:
        chunk_ms = (CHUNK_SAMPLES / SAMPLE_RATE) * 1000
        return max(1, round(self.gate_hold_ms / chunk_ms))

    def _gate_ramp_step(self, ms: float) -> float:
        """Per-chunk gain delta for attack/release envelope."""
        chunk_ms = (CHUNK_SAMPLES / SAMPLE_RATE) * 1000
        frames = max(1.0, ms / chunk_ms)
        return 1.0 / frames

    @property
    def has_clients(self):
        return len(self._clients) > 0

    def start(self, loop):
        if self._running:
            return
        self._loop = loop
        self._running = True

        try:
            self._process = subprocess.Popen(
                [
                    "arecord",
                    "-D", AIOC_DEVICE,
                    "-f", "S16_LE",
                    "-r", str(SAMPLE_RATE),
                    "-c", str(CHANNELS),
                    "-t", "raw",
                    "--buffer-size", str(CHUNK_SAMPLES),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            log.info(f"arecord started (device={AIOC_DEVICE}, ulaw, 20ms chunks)")
        except FileNotFoundError:
            log.error("arecord not found")
            self._running = False
            return
        except Exception as e:
            log.error(f"Failed to start arecord: {e}")
            self._running = False
            return

        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=2)
            except Exception:
                self._process.kill()
            self._process = None
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
        log.info("RX pipeline stopped")

    def _capture_loop(self):
        log.info("RX capture loop started (ulaw, 20ms)")
        chunk_count = 0
        last_log = time.time()

        while self._running and self._process:
            try:
                pcm_data = self._process.stdout.read(CHUNK_BYTES_PCM)
                if not pcm_data or len(pcm_data) < CHUNK_BYTES_PCM:
                    if not self._running:
                        break
                    log.warning(f"Short read ({len(pcm_data)} bytes)")
                    time.sleep(0.01)
                    continue

                chunk_count += 1

                # Noise gate with soft attack/release (avoids punch on open/close)
                if self.squelch_enabled:
                    import array
                    samples = array.array("h", pcm_data)
                    rms = math.sqrt(sum(s * s for s in samples) / len(samples))
                    audio_open = rms > self.squelch_threshold
                    signal_open = self._signal_present()

                    # OR gate: open on RF (RSSI) or line audio; hold while either is present
                    if signal_open or audio_open:
                        want_open = True
                        self._gate_hold_frames = self._gate_hold_count()
                    elif self._gate_hold_frames > 0:
                        want_open = True
                        self._gate_hold_frames -= 1
                    else:
                        want_open = False

                    start_gain = self._gate_gain
                    if want_open:
                        end_gain = min(1.0, start_gain + self._gate_ramp_step(self.gate_attack_ms))
                    else:
                        end_gain = max(0.0, start_gain - self._gate_ramp_step(self.gate_release_ms))

                    self._gate_gain = end_gain
                    if end_gain <= 0.001:
                        continue

                    pcm_data = _apply_gain_ramp_pcm(pcm_data, start_gain, end_gain)

                ulaw_data = pcm_to_ulaw(pcm_data)

                if self._clients and self._loop:
                    self._loop.call_soon_threadsafe(
                        lambda d=ulaw_data: asyncio.ensure_future(self._broadcast(d))
                    )

                now = time.time()
                if now - last_log >= 5.0:
                    rate = chunk_count / (now - last_log)
                    log.info(f"RX audio: {rate:.1f} ulaw chunks/s, {len(self._clients)} clients")
                    chunk_count = 0
                    last_log = now

            except Exception as e:
                if self._running:
                    log.error(f"RX capture error: {e}")
                    time.sleep(0.1)

    async def _broadcast(self, data: bytes):
        dead = []
        for ws in list(self._clients):
            try:
                await ws.send_bytes(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)
