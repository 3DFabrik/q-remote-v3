"""Quansheng UV-K5 serial protocol handler.

Implements the binary protocol for communicating with the UV-K5:
- Packet framing with XOR encryption
- CRC-16/CCITT validation
- Command building and parsing
- UI text packet parsing (0xB5 frames)

Reference: docs/quansheng-serial-protocol.md (in the ham-remote project)
"""

import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import Optional

# ─── XOR Encryption Key ───────────────────────────────────────────────

XOR_KEY = bytes([
    0x16, 0x6c, 0x14, 0xe6, 0x2e, 0x91, 0x0d, 0x40,
    0x21, 0x35, 0xd5, 0x40, 0x13, 0x03, 0xe9, 0x80
])


# ─── Command IDs ──────────────────────────────────────────────────────

class Cmd(IntEnum):
    """Quansheng UV-K5 command IDs."""
    # Host → Radio
    HELLO           = 0x0514
    GET_RSSI        = 0x0527
    READ_EEPROM     = 0x051B
    WRITE_EEPROM    = 0x051D
    KEY_PRESS       = 0x0801
    GET_SCREEN      = 0x0803
    SCAN            = 0x0808
    SCAN_ADJUST     = 0x0809
    WRITE_REGISTERS = 0x0850
    READ_REGISTERS  = 0x0851
    WRITE_GPIO      = 0x0860
    READ_GPIO       = 0x0861
    GPIO_PULSE      = 0x0862
    ENTER_HW_MODE   = 0x0870
    EXIT_HW_MODE    = 0x0871
    SET_REPORT_REG  = 0x0872
    TX_AUX_873      = 0x0873  # Used in TX sequence
    TX_END_876      = 0x0876  # Used in TX end sequence

    # Radio → Host
    IM_HERE           = 0x0515
    RSSI_INFO         = 0x0528
    READ_EEPROM_REPLY = 0x051C
    WRITE_EEPROM_REPLY= 0x051E
    SCAN_REPLY        = 0x0908
    REGISTER_INFO     = 0x0951
    GPIO_INFO         = 0x0961


class Key(IntEnum):
    """Key codes for KeyPress command (0x0801)."""
    KEY_0   = 0
    KEY_1   = 1
    KEY_2   = 2
    KEY_3   = 3
    KEY_4   = 4
    KEY_5   = 5
    KEY_6   = 6
    KEY_7   = 7
    KEY_8   = 8
    KEY_9   = 9
    KEY_A   = 10   # Function A / menu F1
    KEY_B   = 11   # Function B / F2
    KEY_C   = 12   # Function C / scan / F3
    MENU    = 13
    UP      = 14
    DOWN    = 15
    PTT     = 16
    KEY_17  = 17   # Unknown
    KEY_18  = 18   # Unknown
    EXIT    = 19   # Also used as PTT release


# ─── CRC-16/CCITT ─────────────────────────────────────────────────────

def crc16_byte(byte: int, crc: int = 0) -> int:
    """Compute CRC-16/CCITT for a single byte."""
    crc ^= byte << 8
    for _ in range(8):
        crc <<= 1
        if crc > 0xFFFF:
            crc ^= 0x1021
        crc &= 0xFFFF
    return crc


def crc16(data: bytes, initial: int = 0) -> int:
    """Compute CRC-16/CCITT for a byte sequence."""
    crc = initial
    for b in data:
        crc = crc16_byte(b, crc)
    return crc


# ─── XOR Encryption/Decryption ────────────────────────────────────────

def xor_crypt(data: bytes, start_index: int = 0) -> bytearray:
    """XOR encrypt/decrypt data with the rotating key."""
    result = bytearray(len(data))
    for i, b in enumerate(data):
        result[i] = b ^ XOR_KEY[(start_index + i) & 0x0F]
    return result


# ─── Packet Building ──────────────────────────────────────────────────

# Frame markers
HEADER = b'\xAB\xCD'
FOOTER = b'\xDC\xBA'

UI_MARKER = 0xB5  # UI text packets start with this


@dataclass
class Packet:
    """Parsed binary protocol packet."""
    cmd: Cmd
    params: bytes


def build_packet(cmd_id: int, params: bytes = b'') -> bytes:
    """Build a complete protocol frame for sending to the radio.
    
    Matches V1's build_packet exactly.
    """
    data = bytearray(512)
    data[0] = 0xAB
    data[1] = 0xCD
    data[4] = cmd_id & 0xFF
    data[5] = (cmd_id >> 8) & 0xFF
    ind = 8
    
    for b in params:
        data[ind] = b
        ind += 1
    
    prm_len = ind - 8
    data[6] = prm_len & 0xFF
    data[7] = (prm_len >> 8) & 0xFF
    
    # Encrypt payload + compute CRC
    crc = 0
    xor_idx = 0
    for i in range(4, ind):
        crc = crc16_byte(data[i], crc)
        data[i] = data[i] ^ XOR_KEY[xor_idx & 0x0F]
        xor_idx += 1
    
    # Append encrypted CRC
    data[ind] = (crc & 0xFF) ^ XOR_KEY[xor_idx & 0x0F]
    xor_idx += 1
    data[ind + 1] = ((crc >> 8) & 0xFF) ^ XOR_KEY[xor_idx & 0x0F]
    ind += 2
    
    # Footer
    data[ind] = 0xDC
    data[ind + 1] = 0xBA
    ind += 2
    
    # Size field (V1 formula)
    data_len = prm_len + 4  # cmd(2) + paramLen(2) + args(prm_len)
    data[2] = data_len & 0xFF
    data[3] = (data_len >> 8) & 0xFF
    
    return bytes(data[:ind])


def build_key_press(keycode: int) -> bytes:
    """Build a KeyPress packet with magic timestamp.
    
    V1 sends: u16(keycode) + uint32(0x12345678)
    """
    params = struct.pack('<H', keycode) + struct.pack('<I', 0x12345678)
    return build_packet(Cmd.KEY_PRESS, params)


def build_hello(timestamp: int = 0x12345678) -> bytes:
    """Build a Hello/heartbeat packet."""
    return build_packet(Cmd.HELLO, struct.pack('<I', timestamp))


def build_get_rssi() -> bytes:
    """Build an RSSI request packet."""
    return build_packet(Cmd.GET_RSSI, struct.pack('<I', 0x12345678))


def build_get_screen() -> bytes:
    """Build a screen dump request packet."""
    return build_packet(Cmd.GET_SCREEN, struct.pack('<I', 0x12345678))


# ─── Packet Parsing ───────────────────────────────────────────────────

class ParseState(IntEnum):
    """State machine states for packet parsing."""
    IDLE = 0
    GOT_AB = 1
    GOT_B5 = 2       # UI text packet
    LEN_LSB = 3
    LEN_MSB = 4
    DATA = 5
    CRC_LSB = 6
    CRC_MSB = 7
    GOT_DC = 8


class PacketParser:
    """Stateful parser for incoming serial data.
    
    Handles both binary protocol packets (0xABCD header) and
    UI text packets (0xB5 header).
    
    Usage:
        parser = PacketParser()
        parser.on_packet = lambda pkt: print(pkt)
        parser.on_ui_data = lambda data: print(data)
        parser.feed(serial_data)
    """
    
    def __init__(self):
        self.state = ParseState.IDLE
        self.data = bytearray()
        self.expected_len = 0
        self.crc = 0
        
        # Callbacks
        self.on_packet: Optional[callable] = None   # Called with Packet for binary packets
        self.on_ui_data: Optional[callable] = None   # Called with raw bytes for UI packets
        
        # Stats
        self.packets_parsed = 0
        self.parse_errors = 0
    
    def feed(self, data: bytes) -> list:
        """Feed raw serial data into the parser.
        
        Returns list of parsed Packets (binary protocol only; UI data
        goes to on_ui_data callback).
        """
        results = []
        
        for byte in data:
            result = self._process_byte(byte)
            if result is not None:
                results.append(result)
        
        return results
    
    def reset(self) -> None:
        """Reset parser state."""
        self.state = ParseState.IDLE
        self.data = bytearray()
        self.expected_len = 0
    
    def _process_byte(self, byte: int) -> Optional[Packet]:
        """Process a single byte through the state machine."""
        
        if self.state == ParseState.IDLE:
            if byte == 0xAB:
                self.state = ParseState.GOT_AB
            elif byte == UI_MARKER:
                self.state = ParseState.GOT_B5
                self.data = bytearray()
            return None
        
        elif self.state == ParseState.GOT_AB:
            if byte == 0xCD:
                self.state = ParseState.LEN_LSB
                self.data = bytearray()
                self.expected_len = 0
            else:
                self.state = ParseState.IDLE
                self.parse_errors += 1
            return None
        
        elif self.state == ParseState.GOT_B5:
            # UI text packet: 0xB5 <type> <val1> <val2> <val3> <dataLen> <data[]>
            self.data.append(byte)
            if len(self.data) >= 6:
                data_len = self.data[5]
                total_expected = 6 + data_len
                if len(self.data) >= total_expected:
                    # Complete UI packet
                    if self.on_ui_data:
                        self.on_ui_data(bytes(self.data))
                    self.state = ParseState.IDLE
                    self.data = bytearray()
            return None
        
        elif self.state == ParseState.LEN_LSB:
            self.expected_len = byte
            self.state = ParseState.LEN_MSB
            return None
        
        elif self.state == ParseState.LEN_MSB:
            self.expected_len |= byte << 8
            self.data = bytearray()
            self.state = ParseState.DATA
            return None
        
        elif self.state == ParseState.DATA:
            self.data.append(byte)
            # Data length = expected_len - 2 (footer) - 2 (CRC) = expected_len - 4
            data_len = self.expected_len - 4
            if len(self.data) >= self.expected_len - 2:  # data + CRC
                self.state = ParseState.GOT_DC
            return None
        
        elif self.state == ParseState.GOT_DC:
            if byte == 0xDC:
                self.state = ParseState.IDLE  # Will check for BA next
                # Actually need to check for BA
                self.state = 99  # Waiting for BA
            else:
                self.state = ParseState.IDLE
                self.parse_errors += 1
            return None
        
        elif self.state == 99:  # Waiting for 0xBA after 0xDC
            self.state = ParseState.IDLE
            if byte == 0xBA:
                # Complete packet! Decrypt and parse
                # data contains: encrypted(cmd + param_len + params + crc)
                # but we also consumed 2 bytes for CRC via GOT_DC state... let me re-check
                
                # Actually, in DATA state we consumed everything up to expected_len - 2
                # Then GOT_DC consumed one more, and here we consumed the last
                # So self.data has encrypted(cmd + param_len + params) without CRC
                # Wait no - let me re-think the state machine...
                
                # The received data after length field:
                # encrypted(cmd_id + param_len + params + crc16) + 0xDC + 0xBA
                # expected_len counts from after length to... let me check
                # LENGTH = param_len + 4 + 4 = param_len + 8? No...
                # From doc: LENGTH = total_bytes_after_header
                # So: LENGTH = len(encrypted_payload_with_crc) + 2(footer)
                # encrypted_payload_with_crc = cmd(2) + param_len(2) + params(N) + crc(2) = N+6
                # So LENGTH = N + 6 + 2 = N + 8
                # Data we read in DATA state: LENGTH - 4 (footer 2 + ?)
                # Hmm, let me just count bytes properly...
                
                # After header(2) + length(2), we have:
                # encrypted[cmd(2) + paramlen(2) + params(N) + crc(2)] + footer(2)
                # Total after header+length = N+6+2 = N+8
                # expected_len = N+8
                # We consumed in DATA state until len(data) >= expected_len - 2
                # So data has N+6 bytes = cmd + paramlen + params + crc (encrypted)
                # Then GOT_DC = 0xDC (footer byte 1), and this = 0xBA (footer byte 2)
                
                return self._decrypt_and_parse(self.data)
            else:
                self.parse_errors += 1
            return None
        
        return None
    
    def _decrypt_and_parse(self, encrypted: bytes) -> Optional[Packet]:
        """Decrypt and parse a complete packet payload."""
        try:
            # Decrypt
            decrypted = xor_crypt(encrypted, start_index=0)
            
            # Parse
            if len(decrypted) < 6:  # cmd(2) + paramlen(2) + crc(2)
                self.parse_errors += 1
                return None
            
            cmd_id = struct.unpack_from('<H', decrypted, 0)[0]
            param_len = struct.unpack_from('<H', decrypted, 2)[0]
            
            # Verify CRC
            payload = decrypted[:4 + param_len]  # cmd + paramlen + params
            expected_crc = struct.unpack_from('<H', decrypted, 4 + param_len)[0]
            actual_crc = crc16(payload)
            
            if actual_crc != expected_crc:
                self.parse_errors += 1
                return None
            
            params = decrypted[4:4 + param_len]
            
            try:
                cmd = Cmd(cmd_id)
            except ValueError:
                cmd = cmd_id  # Unknown command, keep as int
            
            self.packets_parsed += 1
            return Packet(cmd=cmd, params=bytes(params))
        
        except Exception:
            self.parse_errors += 1
            return None


def rssi_to_dbm(rssi_raw: int) -> float:
    """Convert raw RSSI value to dBm.
    
    BK4819 RSSI register 0x67, 10-bit value.
    Formula: dBm = -rssi_raw / 2.0 (approximate)
    """
    return -(rssi_raw & 0x3FF) / 2.0


def dbm_to_s_unit(dbm: float) -> str:
    """Convert dBm value to S-unit string."""
    s_points = [
        (-121, "S1"), (-115, "S2"), (-109, "S3"), (-103, "S4"),
        (-97, "S5"), (-91, "S6"), (-85, "S7"), (-79, "S8"),
        (-73, "S9"),
    ]
    for threshold, label in s_points:
        if dbm <= threshold:
            return label
    
    # S9+
    over = dbm - (-73)
    if over < 10:
        return "S9+10"
    elif over < 20:
        return "S9+20"
    elif over < 30:
        return "S9+30"
    else:
        return f"S9+{int(over)}"
