"""BK4819 / QuanshengDock RSSI, band correction, and S-meter conversion."""

from __future__ import annotations

# QuanshengDock ui/main.c — dBmCorrTable[gRxVfo->Band]
DBM_CORR_TABLE: tuple[int, ...] = (-15, -25, -20, -4, -7, -6, -1)

# egzumer frequencyBandTable lower bounds (frequency in 10 Hz units)
_BAND_LOWERS: tuple[int, ...] = (
    5_000_000,    # BAND1  ~50 MHz
    47_000_000,   # BAND7  ~470 MHz
    10_800_000,   # BAND2  ~108 MHz
    13_700_000,   # BAND3  ~137 MHz
    17_400_000,   # BAND4  ~174 MHz
    35_000_000,   # BAND5  ~350 MHz
    40_000_000,   # BAND6  ~400 MHz
)

# QuanshengDock / egzumer EEPROM defaults (S0_LEVEL, S9_LEVEL)
S0_LEVEL = 130
S9_LEVEL = 76

# BK4819 reg 0x02 interrupt / status bits (bk4819-regs.h)
REG02_SQUELCH_FOUND = 1 << 3
REG02_SQUELCH_LOST = 1 << 2


def mhz_to_band(mhz: float) -> int:
    """Map RX frequency to VFO band index (FREQUENCY_GetBand)."""
    freq = int(mhz * 100_000)  # firmware 10 Hz units
    for band in range(len(_BAND_LOWERS) - 1, -1, -1):
        if freq >= _BAND_LOWERS[band]:
            return band
    return 0


def raw_to_dbm(raw: int) -> int:
    """Hardware RSSI (9-bit) → uncorrected dBm. Same as BK4819_GetRSSI_dBm()."""
    r = raw & 0x1FF
    return (r // 2) - 160


def corrected_dbm(raw: int, band: int = 0) -> int:
    """RSSI dBm with QuanshengDock band correction table."""
    b = max(0, min(len(DBM_CORR_TABLE) - 1, int(band)))
    return raw_to_dbm(raw) + DBM_CORR_TABLE[b]


def dbm_to_s_raw(dbm: int) -> float:
    """Map corrected dBm to 0..15 S-meter scale (QuanshengDock ui/main.c RSSI bar)."""
    s0_dbm = -S0_LEVEL
    span = S0_LEVEL - S9_LEVEL
    if span <= 0:
        return 0.0

    s_level = max(0, min(9, (dbm - s0_dbm) * 9 // span))
    over_s9_dbm = max(0, min(99, dbm + S9_LEVEL))
    over_s9_bars = min(over_s9_dbm // 10, 4)
    return firmware_smeter_to_s_raw(s_level, over_s9_bars)


def firmware_smeter_to_s_raw(s_level: int, over_s9_bars: int) -> float:
    """UI type 8: val1=s_level (0..9), val2=overS9Bars (0..4). Matches CRT bar/text."""
    s_level = max(0, min(9, int(s_level)))
    over_s9_bars = max(0, min(4, int(over_s9_bars)))
    if over_s9_bars == 0:
        return float(s_level)
    return min(15.0, 9.0 + float(over_s9_bars))


def raw_to_s_raw(raw: int, band: int = 0) -> float:
    """BK4819 reg 0x67 → S-meter needle value with band-corrected dBm."""
    return dbm_to_s_raw(corrected_dbm(raw, band))


def parse_rssi_info(data: bytes) -> tuple[int, int, int] | None:
    """Parse 0x528 RSSI_INFO: reg 0x67, 0x65 (noise), 0x63 (glitch)."""
    if len(data) < 6:
        return None
    raw = (data[4] | (data[5] << 8)) & 0x1FF
    noise = data[6] & 0xFF if len(data) > 6 else 0
    glitch = data[7] & 0xFF if len(data) > 7 else 0
    return raw, noise, glitch


def squelch_open_from_reg02(reg02: int, previous: bool) -> bool:
    """Track squelch like the MCU: FOUND opens, LOST closes, else hold state."""
    # Flags may appear in either byte depending on REGISTER_INFO wire order.
    flags = (reg02 & 0xFF) | ((reg02 >> 8) & 0xFF)
    if flags & REG02_SQUELCH_FOUND:
        return True
    if flags & REG02_SQUELCH_LOST:
        return False
    return previous
