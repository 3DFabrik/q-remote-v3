# EEPROM Structure — Quansheng UV-K5

Reverse-engineered from [Nicsure's QuanshengDock](https://github.com/nicsure/QuanshengDock).

## Memory Regions

| Region   | Start   | End     | Size   | Content                         |
|----------|---------|---------|--------|---------------------------------|
| Data     | 0x0000  | 0x0C7F  | 3200 B | 200 channels × 16 bytes each   |
| Attr     | 0x0D60  | 0x0E27  | 200 B  | 200 × 1 byte per channel       |
| Names    | 0x0F50  | 0x1BCF  | 3200 B | 200 channel names × 16 bytes   |

**Total EEPROM for channels:** 6600 bytes

## Per-Channel Data (16 bytes)

Offset = channel_number × 16 (channel 0 = first channel)

| Byte | Bits      | Field        | Values                                    |
|------|-----------|--------------|-------------------------------------------|
| 0-3  | all       | RxFreq       | uint32 LE, in 10 Hz units (14476250 = 144.76250 MHz) |
| 4-7  | all       | TxOffset     | uint32 LE, in 10 Hz units                 |
| 8    | all       | RxCode       | CTCSS index (0-50) or DCS index (0-104)   |
| 9    | all       | TxCode       | CTCSS index (0-50) or DCS index (0-104)   |
| 10   | 7-4       | TxCodeType   | 0=None, 1=CTCSS, 2=DCS, 3=ReverseDCS      |
| 10   | 3-0       | RxCodeType   | 0=None, 1=CTCSS, 2=DCS, 3=ReverseDCS      |
| 11   | 7-4       | Modulation   | 0=FM, 1=AM, 2=USB                         |
| 11   | 3-0       | OffsetDir    | 0=Off, 1=+, 2=-                           |
| 12   | 7-4       | BusyLock     | 0 or 1                                    |
| 12   | 3-2       | OutputPower  | 0=High, 1=Mid, 2=Low                      |
| 12   | 1         | Bandwidth    | 0=Wide, 1=Narrow                          |
| 12   | 0         | Reverse      | 0 or 1                                    |
| 13   | 3-1       | PttId        | 0=Off, 1=BOT, 2=EOT, 3=Both               |
| 13   | 0         | Dtmf         | 0 or 1                                    |
| 14   | all       | Step         | Frequency step index (see table below)    |
| 15   | all       | Scramble     | 0=Off, 1-10 = 2600Hz-3500Hz               |

## Attr Byte (1 byte per channel)

| Bit(s) | Field      | Values                                    |
|--------|------------|-------------------------------------------|
| 7      | Scanlist1  | 0 or 1                                    |
| 6      | Scanlist2  | 0 or 1                                    |
| 5-4    | Compander  | 0=Off, 1=TX, 2=RX, 3=Both                |
| 3-0    | Band       | Derived from frequency: 0-6 = in use, 15 = cleared |

Combined Scanlist = (Scanlist1 ? 1 : 0) \| (Scanlist2 ? 2 : 0) → 0=None, 1=List1, 2=List2, 3=Both

## Name Region (16 bytes per channel)

ASCII characters, padded with 0x20 (space) then 0x00 (null). Names are at offset `0x0F50 + channel_number × 16`.

## Frequency Step Table (Byte 14)

| Index | Step     | Index | Step     | Index | Step     |
|-------|----------|-------|----------|-------|----------|
| 0     | 2.5 kHz  | 7     | 0.01 kHz | 14    | 15 kHz   |
| 1     | 5 kHz    | 8     | 0.05 kHz | 15    | 30 kHz   |
| 2     | 6.25 kHz | 9     | 0.1 kHz  | 16    | 50 kHz   |
| 3     | 10 kHz   | 10    | 0.25 kHz | 17    | 100 kHz  |
| 4     | 12.5 kHz | 11    | 0.5 kHz  | 18    | 125 kHz  |
| 5     | 25 kHz   | 12    | 1 kHz    | 19    | 250 kHz  |
| 6     | 8.33 kHz | 13    | 1.25 kHz | 20    | 500 kHz  |

## CTCSS Tones (50 standard, index 0-49)

| Idx | Hz    | Idx | Hz    | Idx | Hz    | Idx | Hz    | Idx | Hz    |
|-----|-------|-----|-------|-----|-------|-----|-------|-----|-------|
| 0   | 67.0  | 10  | 94.8  | 20  | 131.8 | 30  | 171.3 | 40  | 203.5 |
| 1   | 69.3  | 11  | 97.4  | 21  | 136.5 | 31  | 173.8 | 41  | 206.5 |
| 2   | 71.9  | 12  | 100.0 | 22  | 141.3 | 32  | 177.3 | 42  | 210.7 |
| 3   | 74.4  | 13  | 103.5 | 23  | 146.2 | 33  | 179.9 | 43  | 218.1 |
| 4   | 77.0  | 14  | 107.2 | 24  | 151.4 | 34  | 183.5 | 44  | 225.7 |
| 5   | 79.7  | 15  | 110.9 | 25  | 156.7 | 35  | 186.2 | 45  | 229.1 |
| 6   | 82.5  | 16  | 114.8 | 26  | 159.8 | 36  | 189.9 | 46  | 233.6 |
| 7   | 85.4  | 17  | 118.8 | 27  | 162.2 | 37  | 192.8 | 47  | 241.8 |
| 8   | 88.5  | 18  | 123.0 | 28  | 165.5 | 38  | 196.6 | 48  | 250.3 |
| 9   | 91.5  | 19  | 127.3 | 29  | 167.9 | 39  | 199.5 | 49  | 254.1 |

## EEPROM Read Sequence

Serial protocol at 38400 baud, 8N1. EEPROM commands only work when Radio is **not** in Remote UI mode (no Hello packet sent).

1. Send `READ_EEPROM` packet: `cmd=0x051B, u16(offset), u16(size), timestamp(u32)`
2. Receive `READ_EEPROM_REPLY`: `[cmd:2][paramLen:2][offset:2][???:1][size:1][data...]`
3. Data payload starts at byte offset 8 in the reply packet
4. Read in chunks of 128 bytes: data region (0x0000-0x0C7F) → attr (0x0D60-0x0E27) → names (0x0F50-0x1BCF)

## EEPROM Write Sequence

1. Send `WRITE_EEPROM` packet: `cmd=0x051D, u16(offset), 1(u32), timestamp(u32), data(bytes)`
2. Receive `WRITE_EEPROM_REPLY`: `[cmd:2][paramLen:2][offset:2]`
3. Same chunk sequence as read, add ~50ms delay between writes

## Backup File Format (.chan)

6600 bytes total: `data(3200) + names(3200) + attr(200)`
