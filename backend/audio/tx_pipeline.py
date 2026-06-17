"""
TX Audio Pipeline: WebSocket → μ-law decode → aplay + relay to other clients

Receives μ-law audio from browser mic, plays it through the radio's speaker
via the AIOC sound card, AND forwards the raw μ-law audio to all other
connected clients (network PTT audio).
"""

import asyncio
import logging
import subprocess
import struct
import time

log = logging.getLogger(__name__)

# μ-law decode table (standard G.711)
_ULAW_DECODE = bytearray(256)
for i in range(256):
    u = ~i & 0xFF
    t = ((u & 0x0F) << 3) + 0x84
    t <<= (u >> 4) & 0x07
    pcm = (0x84 - t) if (u & 0x80) else (t - 0x84)
    _ULAW_DECODE[i] = pcm & 0xFF  # Low byte only for table init

# Proper 16-bit decode table
_ULAW_TO_PCM = [0] * 256
for i in range(256):
    u = ~i & 0xFF
    t = ((u & 0x0F) << 3) + 0x84
    t <<= (u >> 4) & 0x07
    _ULAW_TO_PCM[i] = (0x84 - t) if (u & 0x80) else (t - 0x84)


def ulaw_to_pcm(ulaw_data: bytes) -> bytes:
    """Decode μ-law bytes to 16-bit LE PCM."""
    n = len(ulaw_data)
    pcm = bytearray(n * 2)
    for i in range(n):
        val = _ULAW_TO_PCM[ulaw_data[i]]
        struct.pack_into('<h', pcm, i * 2, val)
    return bytes(pcm)


class TxPipeline:
    def __init__(self, device='hw:CARD=AllInOneCable,DEV=0'):
        self.device = device
        self._aplay = None
        self._clients = set()          # all TX websockets
        self._loop = None
        self.is_transmitting = False
        self._last_chunk_time = 0.0

        # Network relay: map of rx_websocket → (rx_ws, tx_ws_to_exclude)
        # Set by app.py via set_relay_targets()
        self._relay_clients = set()    # RX websockets that should receive PTT audio
        self._active_tx_ws = None      # WebSocket of the client currently sending

    def start(self):
        self._loop = asyncio.get_event_loop()
        log.info("TX pipeline ready")

    def set_relay_targets(self, rx_clients: set):
        """Called by app.py to provide the RX client set for audio relay."""
        self._relay_clients = rx_clients

    async def add_client(self, ws):
        self._clients.add(ws)
        log.info(f"TX client added, total: {len(self._clients)}")

    async def remove_client(self, ws):
        self._clients.discard(ws)
        if self._active_tx_ws is ws:
            self._active_tx_ws = None
        log.info(f"TX client removed, total: {len(self._clients)}")
        if not self._clients:
            self._stop_aplay()

    async def handle_audio(self, ws, data: bytes):
        """Handle incoming μ-law audio from browser.

        1. Decode to PCM and play through radio (aplay)
        2. Forward raw μ-law to all RX clients except the sender
        """
        if not data:
            return

        self._active_tx_ws = ws

        # --- 1. Play through radio ---
        if self._aplay is None or self._aplay.poll() is not None:
            self._start_aplay()

        pcm = ulaw_to_pcm(data)
        try:
            self._aplay.stdin.write(pcm)
            self._aplay.stdin.flush()
        except Exception as e:
            log.error(f"aplay write error: {e}")
            self._stop_aplay()

        # --- 2. Relay to all RX clients (network PTT audio) ---
        # Echo suppression is handled client-side: the sender's browser
        # mutes its RX audio while PTT is active.
        if self._relay_clients and self._loop:
            self._loop.call_soon_threadsafe(
                lambda d=data: asyncio.ensure_future(self._relay(d))
            )

    async def _relay(self, ulaw_data: bytes):
        """Send μ-law audio to all RX clients."""
        for rx_ws in list(self._relay_clients):
            try:
                await rx_ws.send_bytes(ulaw_data)
            except Exception:
                pass  # RxPipeline manages lifecycle

    def _start_aplay(self):
        """Start aplay subprocess for audio output."""
        try:
            self._aplay = subprocess.Popen(
                [
                    'aplay',
                    '-D', self.device,
                    '-f', 'S16_LE',
                    '-r', '8000',
                    '-c', '1',
                    '-t', 'raw',
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.is_transmitting = True
            log.info(f"aplay started (device={self.device})")
        except Exception as e:
            log.error(f"Failed to start aplay: {e}")
            self._aplay = None

    def _stop_aplay(self):
        """Stop aplay subprocess."""
        if self._aplay:
            try:
                self._aplay.stdin.close()
                self._aplay.terminate()
                self._aplay.wait(timeout=2)
            except Exception:
                self._aplay.kill()
            self._aplay = None
            self.is_transmitting = False
            log.info("aplay stopped")

    def stop(self):
        self._stop_aplay()
        self._clients.clear()
        self._active_tx_ws = None
        log.info("TX pipeline stopped")
