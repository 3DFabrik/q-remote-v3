"""RX Audio Pipeline: ALSA capture → μ-law encode → WebSocket clients.

Uses subprocess arecord (no pyaudio dependency) to capture 8kHz 16-bit PCM
from the AIOC USB sound card, encodes to G.711 μ-law, and distributes
raw bytes to connected WebSocket clients.
"""

import asyncio
import logging
import struct
import subprocess
import threading
import time
from typing import Set

log = logging.getLogger(__name__)

SAMPLE_RATE = 8000
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit
CHUNK_SAMPLES = 640  # 80ms at 8kHz
CHUNK_BYTES_PCM = CHUNK_SAMPLES * SAMPLE_WIDTH  # 1280 bytes

AIOC_DEVICE = "hw:CARD=AllInOneCable,DEV=0"

# ── μ-law encoding (G.711) ──────────────────────────────────────────

# Standard μ-law compression: 14-segment, ISO/ITU-T G.711
# Uses the canonical bit-manipulation approach


def _build_ulaw_table():
    """Build a 65536-entry lookup table: unsigned 16-bit index → μ-law byte.
    Index 0 = -32768, index 32768 = 0, index 65535 = 32767.
    """
    BIAS = 0x84   # 132
    CLIP = 32635

    table = bytearray(65536)
    for i in range(65536):
        # Convert unsigned index to signed 16-bit sample
        sample = i if i < 32768 else i - 65536

        # Clip
        if sample > CLIP:
            sample = CLIP
        elif sample < -CLIP:
            sample = -CLIP

        # Sign bit
        sign = 0x80 if sample < 0 else 0x00
        if sign:
            sample = -sample

        sample += BIAS

        # Find segment by threshold
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
    """Convert 16-bit signed PCM bytes to μ-law bytes using lookup table."""
    n = len(pcm_data) // 2
    result = bytearray(n)
    import array
    samples = array.array('h', pcm_data[:n*2])
    for i in range(n):
        # Convert signed (-32768..32767) to unsigned index (0..65535)
        result[i] = _ULAW_TABLE[(samples[i] + 65536) & 0xFFFF if samples[i] < 0 else samples[i]]
    return bytes(result)


class RxPipeline:
    """Captures RX audio from AIOC, encodes to μ-law, streams to WebSocket clients."""

    def __init__(self):
        self._process = None
        self._running = False
        self._thread = None
        self._clients: Set = set()
        self._loop = None

    def add_client(self, websocket):
        """Add a WebSocket client to receive audio."""
        self._clients.add(websocket)
        log.info(f"RX audio client added ({len(self._clients)} total)")

    def remove_client(self, websocket):
        """Remove a WebSocket client."""
        self._clients.discard(websocket)
        log.info(f"RX audio client removed ({len(self._clients)} total)")

    @property
    def has_clients(self):
        return len(self._clients) > 0

    def start(self, loop):
        """Start arecord subprocess and capture thread."""
        if self._running:
            return
        self._loop = loop
        self._running = True

        try:
            self._process = subprocess.Popen(
                [
                    'arecord',
                    '-D', AIOC_DEVICE,
                    '-f', 'S16_LE',   # 16-bit signed little-endian
                    '-r', str(SAMPLE_RATE),
                    '-c', str(CHANNELS),
                    '-t', 'raw',
                    '--buffer-size', str(CHUNK_SAMPLES),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            log.info(f"arecord started (device={AIOC_DEVICE})")
        except FileNotFoundError:
            log.error("arecord not found – install alsa-utils")
            self._running = False
            return
        except Exception as e:
            log.error(f"Failed to start arecord: {e}")
            self._running = False
            return

        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop capture and cleanup."""
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
        """Read PCM from arecord, encode to μ-law, send to clients."""
        log.info("RX capture loop started")
        chunk_count = 0
        last_log = time.time()
        while self._running and self._process:
            try:
                pcm_data = self._process.stdout.read(CHUNK_BYTES_PCM)
                if not pcm_data or len(pcm_data) < CHUNK_BYTES_PCM:
                    if not self._running:
                        break
                    log.warning(f"Short read from arecord ({len(pcm_data)} bytes)")
                    time.sleep(0.01)
                    continue

                # Encode to μ-law
                ulaw_data = pcm_to_ulaw(pcm_data)

                # Log rate every 5 seconds
                chunk_count += 1
                now = time.time()
                if now - last_log >= 5.0:
                    rate = chunk_count / (now - last_log)
                    log.info(f"RX audio: {rate:.1f} chunks/s, {len(self._clients)} clients")
                    chunk_count = 0
                    last_log = now

                # Send to all connected clients
                if self._clients and self._loop:
                    self._loop.call_soon_threadsafe(
                        lambda d=ulaw_data: asyncio.ensure_future(self._broadcast(d))
                    )

            except Exception as e:
                if self._running:
                    log.error(f"RX capture error: {e}")
                    time.sleep(0.1)

    async def _broadcast(self, data: bytes):
        """Send μ-law chunk to all connected WebSocket clients."""
        dead = []
        for ws in list(self._clients):
            try:
                await ws.send_bytes(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)
