"""EEPROM parser/packer for Quansheng UV-K5 channel memory.

Based on Nicsure's QuanshengDock C# reverse engineering.
https://github.com/nicsure/QuanshengDock

EEPROM Memory Layout:
  Data region:  0x0000–0x0C7F (3200 bytes) = 200 channels × 16 bytes
  Attr region:  0x0D60–0x0E27 (200 bytes)  = 1 byte per channel
  Names region: 0x0F50–0x1BCF (3200 bytes) = 200 names × 16 bytes

Per-channel Data bytes (16 bytes):
  [0:4]   RxFreq   uint32 LE  (10 Hz units)
  [4:8]   TxOffset uint32 LE  (10 Hz units)
  [8]     RxCode   byte
  [9]     TxCode   byte
  [10]    (TxCodeType << 4) | RxCodeType
  [11]    (Modulation << 4) | OffsetDir
  [12]    (BusyLock << 4) | (OutputPower << 2) | (Bandwidth << 1) | Reverse
  [13]    (PttId << 1) | Dtmf     (bits 3-1 = PttId, bit 0 = Dtmf)
  [14]    Step     byte
  [15]    Scramble byte

Attr byte per channel:
  bit 7    Scanlist1
  bit 6    Scanlist2
  bits 5-4 Compander
  bits 3-0 Band
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
    1318.0, 136.5, 141.3, 146.2, 151.4, 156.7, 159.8, 162.2, 165.5, 167.9,
    171.3, 173.8, 177.3, 179.9, 183.5, 186.2, 189.9, 192.8, 196.6, 199.5,
    203.5, 206.5, 210.7, 218.1, 225.7, 229.1, 233.6, 2418.0, 250.3, 254.1,
]

FQ_STEPS = [
    "2.5kHz", "5kHz", "6.25kHz", "10kHz", "12.5kHz",
    "25kHz", "8.33kHz", "0.01kHz", "0.05kHz", "0.1kHz",
    "0.25kHz", "0.5kHz", "1kHz", "1.25kHz", "15kHz",
    "30kHz", "50kHz", "100kHz", "125kHz", "250kHz", "500kHz",
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

SCRAMBLE_MAP = {i: "Off" if i == 0 else f"{2600 + i * 100}Hz" for i in range(11)}
SCRAMBLE_REV = {v: k for k, v in SCRAMBLE_MAP.items()}

COMPANDER_MAP = {0: "Off", 1: "TX", 2: "RX", 3: "Both"}
COMPANDER_REV = {v: k for k, v in COMPANDER_MAP.items()}

SCANLIST_MAP = {0: "None", 1: "List 1", 2: "List 2", 3: "Both"}
SCANLIST_REV = {v: k for k, v in SCANLIST_MAP.items()}


# ─── Helpers ──────────────────────────────────────────────────────

def _freq_from_raw(raw_val: int) -> float:
    """Convert uint32 LE in 10Hz units to MHz."""
    return raw_val / 100000.0


def _freq_to_raw(freq_mhz: float) -> int:
    """Convert MHz to uint32 in 10Hz units."""
    return int(round(freq_mhz * 100000)) & 0xFFFFFFFF


def _parse_name(name_bytes: bytes) -> str:
    """Decode a 16-byte name field. Strip trailing 0xFF / 0x00 / 0x20."""
    name = name_bytes.rstrip(b'\xff\x00\x20').decode('ascii', errors='replace').strip()
    return name


def _freq_to_band(freq_mhz: float) -> int:
    """Calculate band index from frequency in MHz (Nicsure-compatible)."""
    if freq_mhz == 0:
        return 15  # Empty channel
    hz = freq_mhz * 100000
    if hz < 10800000:
        return 0   # FM Broadcast
    elif hz < 13700000:
        return 1   # Airband
    elif hz < 17400000:
        return 2   # VHF / 2m
    elif hz < 35000000:
        return 3   # VHF+
    elif hz < 40000000:
        return 4   # 350 MHz
    elif hz < 47000000:
        return 5   # UHF / 70cm
    else:
        return 6   # 470+

def _pack_name(name: str) -> bytes:
    """Encode a name into 16 bytes, padded with 0x20 (space)."""
    encoded = name.encode('ascii', errors='replace')[:16]
    return encoded.ljust(16, b'\x00')


# ─── Channel Parser / Packer ─────────────────────────────────────

def parse_channel(channel_num: int, data: bytes, name: bytes, attr_byte: int) -> dict:
    """Parse a single 16-byte channel data block into a dict.

    Based on Nicsure's Channel.cs field layout.
    """
    rxFreq_raw, txOffset_raw = struct.unpack_from('<II', data, 0)
    rxFreq = _freq_from_raw(rxFreq_raw)
    txOffset = _freq_from_raw(txOffset_raw)

    rxCode = data[8]
    txCode = data[9]

    # Byte 10: (TxCodeType << 4) | RxCodeType
    code_type_byte = data[10]
    rxCodeType = CODE_TYPE_MAP.get(code_type_byte & 0x0F, "None")
    txCodeType = CODE_TYPE_MAP.get((code_type_byte >> 4) & 0x0F, "None")

    # Byte 11: (Modulation << 4) | OffsetDir
    mod_off_byte = data[11]
    offsetDir = OFFSET_DIR_MAP.get(mod_off_byte & 0x0F, "Off")
    modulation = MODULATION_MAP.get((mod_off_byte >> 4) & 0x0F, "FM")

    # Byte 12: (BusyLock << 4) | (OutputPower << 2) | (Bandwidth << 1) | Reverse
    byte12 = data[12]
    busyLock = bool((byte12 >> 4) & 0x0F)
    power = POWER_MAP.get((byte12 >> 2) & 0x03, "High")
    bandwidth = BANDWIDTH_MAP.get((byte12 >> 1) & 0x01, "Wide")
    reverse = bool(byte12 & 0x01)

    # Byte 13: (PttId << 1) | Dtmf
    byte13 = data[13]
    pttId = PTT_ID_MAP.get((byte13 >> 1) & 0x07, "Off")
    dtmf = bool(byte13 & 0x01)

    # Byte 14: Step (full byte)
    stepIdx = data[14]
    step = FQ_STEPS[stepIdx] if stepIdx < len(FQ_STEPS) else f"Unknown({stepIdx})"

    # Byte 15: Scramble (full byte)
    scrambleIdx = data[15]
    scramble = SCRAMBLE_MAP.get(scrambleIdx, f"Unknown({scrambleIdx})")

    # Attr byte: Scanlist1(bit7) | Scanlist2(bit6) | Compander(bits 5-4) | Band(bits 3-0)
    scanlist1 = bool(attr_byte & 0x80)
    scanlist2 = bool(attr_byte & 0x40)
    scanlist = SCANLIST_MAP.get(
        (1 if scanlist1 else 0) | (2 if scanlist2 else 0), "None"
    )
    compander = COMPANDER_MAP.get((attr_byte >> 4) & 0x03, "Off")
    band = attr_byte & 0x0F

    # Channel is in use if it has a valid frequency
    inUse = rxFreq > 0

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
        # Store raw bytes for lossless roundtrip
        "_raw_attr": attr_byte,
        "_raw_name": bytes(name),
    }


def pack_channel(ch: dict) -> tuple[bytes, bytes, int]:
    """Pack a channel dict into (16-byte data, 16-byte name, attr_byte).

    Produces byte-exact output matching Nicsure's layout.
    """
    data = bytearray(16)

    # Bytes 0-7: RX freq + TX offset
    struct.pack_into('<II', data, 0,
                     _freq_to_raw(ch.get("rxFreq", 0)),
                     _freq_to_raw(ch.get("txOffset", 0)))

    # Bytes 8-9: Codes
    data[8] = ch.get("rxCode", 0) & 0xFF
    data[9] = ch.get("txCode", 0) & 0xFF

    # Byte 10: (TxCodeType << 4) | RxCodeType
    rxCT = CODE_TYPE_REV.get(ch.get("rxCodeType", "None"), 0)
    txCT = CODE_TYPE_REV.get(ch.get("txCodeType", "None"), 0)
    data[10] = (txCT << 4) | rxCT

    # Byte 11: (Modulation << 4) | OffsetDir
    offDir = OFFSET_DIR_REV.get(ch.get("offsetDir", "Off"), 0)
    mod = MODULATION_REV.get(ch.get("modulation", "FM"), 0)
    data[11] = (mod << 4) | offDir

    # Byte 12: (BusyLock << 4) | (OutputPower << 2) | (Bandwidth << 1) | Reverse
    busyLock = 1 if ch.get("busyLock", False) else 0
    power = POWER_REV.get(ch.get("power", "High"), 0)
    bw = BANDWIDTH_REV.get(ch.get("bandwidth", "Wide"), 0)
    rev = 1 if ch.get("reverse", False) else 0
    data[12] = (busyLock << 4) | (power << 2) | (bw << 1) | rev

    # Byte 13: (PttId << 1) | Dtmf
    pttId = PTT_ID_REV.get(ch.get("pttId", "Off"), 0)
    dtmf = 1 if ch.get("dtmf", False) else 0
    data[13] = ((pttId & 0x07) << 1) | dtmf

    # Byte 14: Step (full byte)
    stepIdx = 0
    stepName = ch.get("step", "12.5kHz")
    for i, s in enumerate(FQ_STEPS):
        if s == stepName:
            stepIdx = i
            break
    data[14] = stepIdx & 0xFF

    # Byte 15: Scramble (full byte)
    scramble = SCRAMBLE_REV.get(ch.get("scramble", "Off"), 0)
    data[15] = scramble & 0xFF

    # Name (16 bytes) - use raw bytes if available for lossless roundtrip
    raw_name = ch.get("_raw_name")
    if raw_name is not None:
        try:
            raw_name_bytes = bytes(raw_name) if not isinstance(raw_name, (bytes, bytearray)) else raw_name
            if len(raw_name_bytes) == 16:
                name = raw_name_bytes
            else:
                name = _pack_name(ch.get("name", ""))
        except (TypeError, ValueError):
            name = _pack_name(ch.get("name", ""))
    else:
        name = _pack_name(ch.get("name", ""))

    # Attr byte: reconstruct with band auto-calculated from frequency (Nicsure-compatible)
    # Always recalculate band from rxFreq, preserve scanlist/compander from original
    rxFreq = ch.get("rxFreq", 0)
    band = _freq_to_band(rxFreq)

    # Preserve scanlist and compander from raw attr if available, otherwise from parsed fields
    if "_raw_attr" in ch:
        raw = ch["_raw_attr"]
        scanlist1 = bool(raw & 0x80)
        scanlist2 = bool(raw & 0x40)
        compander = (raw >> 4) & 0x03
    else:
        scanlist = SCANLIST_REV.get(ch.get("scanlist", "None"), 0)
        compander = COMPANDER_REV.get(ch.get("compander", "Off"), 0)
        scanlist1 = bool(scanlist & 1)
        scanlist2 = bool(scanlist & 2)

    attr_byte = ((1 if scanlist1 else 0) << 7) | ((1 if scanlist2 else 0) << 6) | (compander << 4) | (band & 0x0F)

    return bytes(data), name, attr_byte


# ─── Bulk Parse / Pack ────────────────────────────────────────────

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

    return bytes(data_buf), bytes(name_buf), bytes(attr_buf)


# ─── Region Helpers ───────────────────────────────────────────────

def get_read_regions() -> list[tuple[int, int]]:
    """Return list of (offset, length) tuples for reading EEPROM in chunks."""
    regions = []
    for off in range(0, DATA_SIZE, CHUNK_SIZE):
        regions.append((DATA_OFFSET + off, min(CHUNK_SIZE, DATA_SIZE - off)))
    for off in range(0, ATTR_SIZE, CHUNK_SIZE):
        regions.append((ATTR_OFFSET + off, min(CHUNK_SIZE, ATTR_SIZE - off)))
    for off in range(0, NAMES_SIZE, CHUNK_SIZE):
        regions.append((NAMES_OFFSET + off, min(CHUNK_SIZE, NAMES_SIZE - off)))
    return regions


def get_write_regions(data: bytes, names: bytes, attrs: bytes) -> list[tuple[int, bytes]]:
    """Return list of (offset, chunk_bytes) for writing EEPROM in chunks."""
    regions = []
    for off in range(0, len(data), CHUNK_SIZE):
        regions.append((DATA_OFFSET + off, data[off:off + CHUNK_SIZE]))
    for off in range(0, len(attrs), CHUNK_SIZE):
        regions.append((ATTR_OFFSET + off, attrs[off:off + CHUNK_SIZE]))
    for off in range(0, len(names), CHUNK_SIZE):
        regions.append((NAMES_OFFSET + off, names[off:off + CHUNK_SIZE]))
    return regions


def validate_channel(ch: dict) -> list[str]:
    """Validate a channel dict. Returns list of error strings (empty = valid)."""
    errors = []
    num = ch.get("number", 0)
    if num < 1 or num > 200:
        errors.append(f"Channel number must be 1-200, got {num}")
    rxFreq = ch.get("rxFreq", 0)
    if rxFreq < 0 or rxFreq > 1300:
        errors.append(f"RX frequency out of range: {rxFreq}")
    name = ch.get("name", "")
    if len(name) > 16:
        errors.append(f"Name too long ({len(name)} chars, max 16)")
    return errors
