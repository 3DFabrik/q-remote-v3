"""EEPROM parser/packer for Quansheng UV-K5 channel memory.

Based on Nicsure's QuanshengDock reverse engineering.

EEPROM Memory Layout:
  Data region:  0x0000–0x0C7F (3200 bytes) = 200 channels × 16 bytes
  Attr region:  0x0D60–0x0E27 (200 bytes)  = 1 byte per channel (band info)
  Names region: 0x0F50–0x1BCF (3200 bytes) = 200 names × 16 bytes
"""

import struct
import logging
from typing import Optional

log = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────

NUM_CHANNELS = 200

DATA_OFFSET = 0x0000
DATA_SIZE = 3200        # 200 × 16

ATTR_OFFSET = 0x0D60
ATTR_SIZE = 200

NAMES_OFFSET = 0x0F50
NAMES_SIZE = 3200        # 200 × 16

# EEPROM read/write chunk size (serial is slow at 38400 baud)
CHUNK_SIZE = 128

CTCSS_TONES = [
    67.0, 69.3, 71.9, 74.4, 77.0, 79.7, 82.5, 85.4, 88.5, 91.5,
    94.8, 97.4, 100.0, 103.5, 107.2, 110.9, 114.8, 118.8, 123.0, 127.3,
    131.8, 136.5, 141.3, 146.2, 151.4, 156.7, 159.8, 162.2, 165.5, 167.9,
    171.3, 173.8, 177.3, 179.9, 183.5, 186.2, 189.9, 192.8, 196.6, 199.5,
    203.5, 206.5, 210.7, 218.1, 225.7, 229.1, 233.6, 241.8, 250.3, 254.1,
]

FQ_STEPS = [
    "2.5kHz", "5kHz", "6.25kHz", "10kHz", "12.5kHz",
    "25kHz", "8.33kHz", "0.01MHz", "0.05MHz", "0.1MHz",
    "0.25MHz", "0.5MHz", "1MHz", "2.5MHz", "5MHz",
    "10MHz", "6.25kHz", "Auto",
]

MODULATION_MAP = {0: "FM", 1: "AM", 2: "USB"}
MODULATION_REV = {v: k for k, v in MODULATION_MAP.items()}

OFFSET_DIR_MAP = {0: "Off", 1: "+", 2: "-"}
OFFSET_DIR_REV = {v: k for k, v in OFFSET_DIR_MAP.items()}

CODE_TYPE_MAP = {0: "None", 1: "CTCSS", 2: "DCS", 3: "ReverseDCS"}
CODE_TYPE_REV = {v: k for k, v in CODE_TYPE_MAP.items()}

POWER_MAP = {0: "High", 1: "Mid", 2: "Low"}
POWER_REV = {v: k for k, v in POWER_MAP.items()}

BANDWIDTH_MAP = {0: "Wide", 1: "Narrow"}
BANDWIDTH_REV = {v: k for k, v in BANDWIDTH_MAP.items()}

PTT_ID_MAP = {0: "Off", 1: "BOT", 2: "EOT", 3: "Both"}
PTT_ID_REV = {v: k for k, v in PTT_ID_MAP.items()}

SCRAMBLE_MAP = {i: "Off" if i == 0 else str(i) for i in range(11)}
SCRAMBLE_REV = {v: k for k, v in SCRAMBLE_MAP.items()}

COMPANDER_MAP = {0: "Off", 1: "TX", 2: "RX", 3: "Both"}
COMPANDER_REV = {v: k for k, v in COMPANDER_MAP.items()}

SCANLIST_MAP = {0: "None", 1: "List 1", 2: "List 2", 3: "Both"}
SCANLIST_REV = {v: k for k, v in SCANLIST_MAP.items()}


def _freq_from_raw(raw_val: int) -> float:
    """Convert uint32 LE in 10Hz units to MHz."""
    return raw_val / 100000.0


def _freq_to_raw(freq_mhz: float) -> int:
    """Convert MHz to uint32 in 10Hz units."""
    return int(round(freq_mhz * 100000)) & 0xFFFFFFFF


def _parse_name(name_bytes: bytes) -> str:
    """Decode a 16-byte name field. Strip trailing 0xFF / 0x00."""
    # Name is typically ASCII, padded with 0xFF or 0x00
    name = name_bytes.rstrip(b'\xff\x00').decode('ascii', errors='replace').strip()
    return name


def _pack_name(name: str) -> bytes:
    """Encode a name into 16 bytes, padded with 0xFF."""
    encoded = name.encode('ascii', errors='replace')[:16]
    return encoded.ljust(16, b'\xff')


def parse_channel(channel_num: int, data: bytes, name: bytes, attr_byte: int) -> dict:
    """Parse a single 16-byte channel data block into a dict."""
    rxFreq_raw, txOffset_raw = struct.unpack_from('<II', data, 0)
    rxFreq = _freq_from_raw(rxFreq_raw)
    txOffset = _freq_from_raw(txOffset_raw)

    rxCode = data[8]
    txCode = data[9]

    codeTypeByte = data[10]
    rxCodeType = CODE_TYPE_MAP.get((codeTypeByte) & 0x0F, "None")
    txCodeType = CODE_TYPE_MAP.get((codeTypeByte >> 4) & 0x0F, "None")

    modOffsetByte = data[11]
    offsetDir = OFFSET_DIR_MAP.get(modOffsetByte & 0x0F, "Off")
    modulation = MODULATION_MAP.get((modOffsetByte >> 4) & 0x0F, "FM")

    stepBusyByte = data[12]
    stepIdx = stepBusyByte & 0x0F
    step = FQ_STEPS[stepIdx] if stepIdx < len(FQ_STEPS) else f"Unknown({stepIdx})"
    busyLock = bool((stepBusyByte >> 4) & 0x0F)

    pttDtmfByte = data[13]
    dtmf = bool(pttDtmfByte & 0x0F)
    pttId = PTT_ID_MAP.get((pttDtmfByte >> 4) & 0x0F, "Off")

    scrambleCompByte = data[14]
    compander = COMPANDER_MAP.get(scrambleCompByte & 0x0F, "Off")
    scrambleIdx = (scrambleCompByte >> 4) & 0x0F
    scramble = SCRAMBLE_MAP.get(scrambleIdx, "Off")

    miscByte = data[15]
    scanlist = SCANLIST_MAP.get(miscByte & 0x03, "None")
    reverse = bool((miscByte >> 2) & 0x01)
    bandwidth = BANDWIDTH_MAP.get((miscByte >> 3) & 0x01, "Wide")
    power = POWER_MAP.get((miscByte >> 4) & 0x03, "High")

    inUse = (attr_byte <= 6 and attr_byte > 0 and rxFreq > 0)
    band = attr_byte if attr_byte <= 6 else 0

    return {
        "number": channel_num,
        "name": _parse_name(name),
        "rxFreq": round(rxFreq, 5),
        "txOffset": round(txOffset, 5),
        "offsetDir": offsetDir,
        "rxCode": rxCode,
        "txCode": txCode,
        "rxCodeType": rxCodeType,
        "txCodeType": txCodeType,
        "modulation": modulation,
        "bandwidth": bandwidth,
        "power": power,
        "step": step,
        "busyLock": busyLock,
        "reverse": reverse,
        "pttId": pttId,
        "dtmf": dtmf,
        "scramble": scramble,
        "compander": compander,
        "scanlist": scanlist,
        "band": band,
        "inUse": inUse,
    }


def pack_channel(ch: dict) -> tuple[bytes, bytes, int]:
    """Pack a channel dict into (16-byte data, 16-byte name, attr_byte)."""
    data = bytearray(16)
    struct.pack_into('<II', data, 0, _freq_to_raw(ch.get("rxFreq", 0)), _freq_to_raw(ch.get("txOffset", 0)))

    data[8] = ch.get("rxCode", 0) & 0xFF
    data[9] = ch.get("txCode", 0) & 0xFF

    rxCT = CODE_TYPE_REV.get(ch.get("rxCodeType", "None"), 0)
    txCT = CODE_TYPE_REV.get(ch.get("txCodeType", "None"), 0)
    data[10] = (txCT << 4) | rxCT

    offDir = OFFSET_DIR_REV.get(ch.get("offsetDir", "Off"), 0)
    mod = MODULATION_REV.get(ch.get("modulation", "FM"), 0)
    data[11] = (mod << 4) | offDir

    stepIdx = 0
    stepName = ch.get("step", "12.5kHz")
    for i, s in enumerate(FQ_STEPS):
        if s == stepName:
            stepIdx = i
            break
    busyLock = 1 if ch.get("busyLock", False) else 0
    data[12] = (busyLock << 4) | stepIdx

    pttId = PTT_ID_REV.get(ch.get("pttId", "Off"), 0)
    dtmf = 1 if ch.get("dtmf", False) else 0
    data[13] = (pttId << 4) | dtmf

    scramble = SCRAMBLE_REV.get(ch.get("scramble", "Off"), 0)
    compander = COMPANDER_REV.get(ch.get("compander", "Off"), 0)
    data[14] = (scramble << 4) | compander

    power = POWER_REV.get(ch.get("power", "High"), 0)
    bw = BANDWIDTH_REV.get(ch.get("bandwidth", "Wide"), 0)
    rev = 1 if ch.get("reverse", False) else 0
    scan = SCANLIST_REV.get(ch.get("scanlist", "None"), 0)
    data[15] = (power << 4) | (bw << 3) | (rev << 2) | scan

    name = _pack_name(ch.get("name", ""))

    band = ch.get("band", 0)
    if not ch.get("inUse", False):
        attr_byte = 0
    else:
        attr_byte = min(max(band, 1), 6)

    return bytes(data), name, attr_byte


def parse_eeprom(data_bytes: bytes, name_bytes: bytes, attr_bytes: bytes) -> list[dict]:
    """Parse full EEPROM regions into list of 200 channel dicts."""
    channels = []
    for i in range(NUM_CHANNELS):
        ch_data = data_bytes[i * 16:(i + 1) * 16]
        ch_name = name_bytes[i * 16:(i + 1) * 16]
        attr_byte = attr_bytes[i] if i < len(attr_bytes) else 0
        channels.append(parse_channel(i + 1, ch_data, ch_name, attr_byte))
    return channels


def pack_channels(channels: list[dict]) -> tuple[bytes, bytes, bytes]:
    """Pack list of channel dicts into (data_bytes, name_bytes, attr_bytes)."""
    data_buf = bytearray(DATA_SIZE)
    name_buf = bytearray(NAMES_SIZE)
    attr_buf = bytearray(ATTR_SIZE)

    for i in range(min(len(channels), NUM_CHANNELS)):
        ch = channels[i]
        ch_data, ch_name, attr_byte = pack_channel(ch)
        data_buf[i * 16:(i + 1) * 16] = ch_data
        name_buf[i * 16:(i + 1) * 16] = ch_name
        attr_buf[i] = attr_byte

    # Fill remaining with empty channels if fewer than 200 provided
    for i in range(len(channels), NUM_CHANNELS):
        empty_data = b'\xff' * 12 + b'\x00' * 4
        empty_name = b'\xff' * 16
        data_buf[i * 16:(i + 1) * 16] = empty_data
        name_buf[i * 16:(i + 1) * 16] = empty_name
        attr_buf[i] = 0

    return bytes(data_buf), bytes(name_buf), bytes(attr_buf)


def get_read_regions() -> list[tuple[int, int]]:
    """Return list of (offset, length) tuples for reading EEPROM in chunks.
    
    Returns chunks of CHUNK_SIZE aligned to the three regions:
      data → attr → names
    """
    regions = []
    # Data region: 0x0000, 3200 bytes
    for off in range(0, DATA_SIZE, CHUNK_SIZE):
        length = min(CHUNK_SIZE, DATA_SIZE - off)
        regions.append((DATA_OFFSET + off, length))
    # Attr region: 0x0D60, 200 bytes
    for off in range(0, ATTR_SIZE, CHUNK_SIZE):
        length = min(CHUNK_SIZE, ATTR_SIZE - off)
        regions.append((ATTR_OFFSET + off, length))
    # Names region: 0x0F50, 3200 bytes
    for off in range(0, NAMES_SIZE, CHUNK_SIZE):
        length = min(CHUNK_SIZE, NAMES_SIZE - off)
        regions.append((NAMES_OFFSET + off, length))
    return regions


def get_write_regions(data: bytes, names: bytes, attrs: bytes) -> list[tuple[int, bytes]]:
    """Return list of (offset, chunk_bytes) for writing EEPROM in chunks."""
    regions = []
    # Data
    for off in range(0, len(data), CHUNK_SIZE):
        chunk = data[off:off + CHUNK_SIZE]
        regions.append((DATA_OFFSET + off, chunk))
    # Attr
    for off in range(0, len(attrs), CHUNK_SIZE):
        chunk = attrs[off:off + CHUNK_SIZE]
        regions.append((ATTR_OFFSET + off, chunk))
    # Names
    for off in range(0, len(names), CHUNK_SIZE):
        chunk = names[off:off + CHUNK_SIZE]
        regions.append((NAMES_OFFSET + off, chunk))
    return regions


def validate_channel(ch: dict) -> list[str]:
    """Validate a channel dict. Returns list of error strings (empty = valid)."""
    errors = []
    num = ch.get("number", 0)
    if num < 1 or num > 200:
        errors.append(f"Channel number must be 1-200, got {num}")

    rxFreq = ch.get("rxFreq", 0)
    if rxFreq <= 0 or rxFreq > 1300:
        errors.append(f"RX frequency out of range: {rxFreq}")

    txOffset = ch.get("txOffset", 0)
    if abs(txOffset) > 1000:
        errors.append(f"TX offset out of range: {txOffset}")

    if ch.get("rxCodeType") in ("CTCSS",) and (ch.get("rxCode", 0) >= len(CTCSS_TONES)):
        errors.append(f"RX CTCSS code index out of range: {ch.get('rxCode')}")

    if ch.get("txCodeType") in ("CTCSS",) and (ch.get("txCode", 0) >= len(CTCSS_TONES)):
        errors.append(f"TX CTCSS code index out of range: {ch.get('txCode')}")

    name = ch.get("name", "")
    if len(name) > 16:
        errors.append(f"Name too long ({len(name)} chars, max 16)")

    return errors
