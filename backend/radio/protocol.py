"""
Quansheng UV-K5 serial protocol - ported from V1.
Uses explicit 2-byte (u16) vs 4-byte (u32) encoding.
"""
import struct


XOR_ARRAY = bytes([
    0x16, 0x6c, 0x14, 0xe6, 0x2e, 0x91, 0x0d, 0x40,
    0x21, 0x35, 0xd5, 0x40, 0x13, 0x03, 0xe9, 0x80
])


class u16:
    """Explicit 16-bit (ushort) value for packet building."""
    __slots__ = ('val',)
    def __init__(self, val):
        self.val = val & 0xFFFF


class Packet:
    HELLO = 0x514
    GET_RSSI = 0x527
    KEY_PRESS = 0x801
    GET_SCREEN = 0x803
    SCAN = 0x808
    SCAN_ADJUST = 0x809
    SCAN_REPLY = 0x908
    WRITE_REGISTERS = 0x850
    READ_REGISTERS = 0x851
    REGISTER_INFO = 0x951
    WRITE_GPIO = 0x860
    READ_GPIO = 0x861
    GPIO_INFO = 0x961
    GPIO_PULSE = 0x862
    ENTER_HW_MODE = 0x0870
    EXIT_HW_MODE = 0x0871
    SET_REPORT_REG = 0x0872
    IM_HERE = 0x515
    RSSI_INFO = 0x528
    WRITE_EEPROM = 0x51D
    WRITE_EEPROM_REPLY = 0x51E
    RESET = 0x05DD
    READ_EEPROM = 0x51B
    READ_EEPROM_REPLY = 0x51C


def crypt(byt, xori):
    return byt ^ XOR_ARRAY[xori & 15]


def crc16(byt, crc):
    crc ^= byt << 8
    for _ in range(8):
        crc <<= 1
        if crc > 0xFFFF:
            crc ^= 0x1021
            crc &= 0xFFFF
    return crc


def build_packet(cmd, *args):
    data = bytearray(512)
    data[0] = 0xAB
    data[1] = 0xCD
    data[4] = cmd & 0xFF
    data[5] = (cmd >> 8) & 0xFF
    ind = 8

    for val in args:
        if isinstance(val, u16):
            data[ind] = val.val & 0xFF
            data[ind + 1] = (val.val >> 8) & 0xFF
            ind += 2
        elif isinstance(val, int):
            data[ind] = val & 0xFF
            data[ind + 1] = (val >> 8) & 0xFF
            data[ind + 2] = (val >> 16) & 0xFF
            data[ind + 3] = (val >> 24) & 0xFF
            ind += 4
        elif isinstance(val, (bytes, bytearray)):
            for b in val:
                data[ind] = b
                ind += 1

    prm_len = ind - 8
    data[6] = prm_len & 0xFF
    data[7] = (prm_len >> 8) & 0xFF

    crc = 0
    xor_idx = 0
    for i in range(4, ind):
        crc = crc16(data[i], crc)
        data[i] = crypt(data[i], xor_idx)
        xor_idx += 1

    data[ind] = crypt(crc & 0xFF, xor_idx)
    xor_idx += 1
    data[ind + 1] = crypt((crc >> 8) & 0xFF, xor_idx)
    ind += 2

    data[ind] = 0xDC
    data[ind + 1] = 0xBA
    ind += 2

    data_len = prm_len + 4
    data[2] = data_len & 0xFF
    data[3] = (data_len >> 8) & 0xFF

    return bytes(data[:ind])


class PacketParser:
    _ST_IDLE = 0
    _ST_CD = 1
    _ST_LEN_LSB = 2
    _ST_LEN_MSB = 3
    _ST_DATA = 4
    _ST_CRC_LSB = 5
    _ST_CRC_MSB = 6
    _ST_DC = 7
    _ST_BA = 8

    def __init__(self):
        self.state = self._ST_IDLE
        self.p_len = 0
        self.p_cnt = 0
        self.data = bytearray()

    def feed(self, raw_bytes, on_command=None, on_ui=None):
        for b in raw_bytes:
            self._process_byte(b, on_command, on_ui)

    def _process_byte(self, b, on_command, on_ui):
        if self.state == self._ST_IDLE:
            if b == 0xAB:
                self.state = self._ST_CD
            elif b == 0xB5:
                self._ui_bytes = bytearray()
                self._ui_bytes.append(b)
                self.state = 100
        elif self.state == self._ST_CD:
            self.state = self._ST_LEN_LSB if b == 0xCD else self._ST_IDLE
        elif self.state == self._ST_LEN_LSB:
            self.p_len = b
            self.state = self._ST_LEN_MSB
        elif self.state == self._ST_LEN_MSB:
            self.p_len |= (b << 8)
            self.p_cnt = 0
            self.data = bytearray(self.p_len)
            self.state = self._ST_DATA
        elif self.state == self._ST_DATA:
            self.data[self.p_cnt] = crypt(b, self.p_cnt)
            self.p_cnt += 1
            if self.p_cnt >= self.p_len:
                self.state = self._ST_CRC_LSB
        elif self.state == self._ST_CRC_LSB:
            self.state = self._ST_CRC_MSB
        elif self.state == self._ST_CRC_MSB:
            self.state = self._ST_DC
        elif self.state == self._ST_DC:
            self.state = self._ST_BA if b == 0xDC else self._ST_IDLE
        elif self.state == self._ST_BA:
            self.state = self._ST_IDLE
            if b == 0xBA and on_command:
                on_command(bytes(self.data))
        elif self.state == 100:
            self._ui_bytes.append(b)
            if len(self._ui_bytes) >= 6:
                ui_type = self._ui_bytes[1]
                data_len = self._ui_bytes[5]
                expected = 6 + data_len
                if len(self._ui_bytes) >= expected:
                    if on_ui:
                        ui_data = bytes(self._ui_bytes[6:6 + data_len])
                        on_ui(
                            ui_type,
                            self._ui_bytes[2],
                            self._ui_bytes[3],
                            self._ui_bytes[4],
                            data_len,
                            ui_data,
                        )
                    self.state = self._ST_IDLE
