"""LCD Display parser for Quansheng UV-K5.

Parses the UI text rendering commands (0xB5 packets) from the radio
into structured drawing instructions that the frontend can render
on a canvas.

UI Packet format: 0xB5 <type> <val1> <val2> <val3> <dataLen> <data[]>
"""

from dataclasses import dataclass
from enum import IntEnum
from typing import Optional


class UIDrawType(IntEnum):
    """UI packet type field."""
    TEXT_LARGE = 0       # Large bold text
    TEXT_NORMAL = 1      # Variable size, normal weight
    TEXT_INVERTED = 2    # Variable size, inverted/bold
    TEXT_MEDIUM = 3      # Medium bold text
    CLEAR_LINES = 5      # Clear line range
    CLEAR_SCREEN = 6     # Clear entire screen
    RECT_FILLED = 7      # Filled rectangle
    RECT_OUTLINE = 8     # Rectangle outline
    KEYPRESS_NOTIFY = 9  # Key press notification from radio
    DTMF_NOTIFY = 10     # DTMF tone notification


@dataclass
class DrawCommand:
    """A single drawing instruction for the LCD display."""
    type: UIDrawType
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0
    text: str = ""
    inverted: bool = False


def parse_ui_packet(data: bytes) -> Optional[DrawCommand]:
    """Parse a single UI rendering packet into a DrawCommand.
    
    Args:
        data: Raw UI packet data (after 0xB5 marker has been stripped).
              Format: <type> <val1> <val2> <val3> <dataLen> <data[]>
    
    Returns:
        DrawCommand or None if the packet can't be parsed.
    """
    if len(data) < 6:
        return None
    
    try:
        draw_type = UIDrawType(data[0])
        val1 = data[1]
        val2 = data[2]
        val3 = data[3]
        data_len = data[4] if len(data) > 4 else data[5]
        text_data = data[5:5 + data_len] if len(data) > 5 else b''
    except (ValueError, IndexError):
        return None
    
    cmd = DrawCommand(type=draw_type)
    
    if draw_type in (UIDrawType.TEXT_LARGE, UIDrawType.TEXT_NORMAL,
                     UIDrawType.TEXT_INVERTED, UIDrawType.TEXT_MEDIUM):
        cmd.x = val1
        cmd.y = val2
        cmd.text = text_data.decode('ascii', errors='replace').rstrip('\x00')
        cmd.inverted = draw_type == UIDrawType.TEXT_INVERTED
    
    elif draw_type == UIDrawType.CLEAR_LINES:
        cmd.y = val1
        cmd.height = val2
    
    elif draw_type == UIDrawType.CLEAR_SCREEN:
        pass  # No params needed
    
    elif draw_type == UIDrawType.RECT_FILLED:
        cmd.x = val1
        cmd.y = val2
        cmd.width = val3
        cmd.height = data_len
    
    elif draw_type == UIDrawType.RECT_OUTLINE:
        cmd.x = val1
        cmd.y = val2
        cmd.width = val3
        cmd.height = data_len
    
    elif draw_type == UIDrawType.KEYPRESS_NOTIFY:
        cmd.x = val1  # Key code that was pressed
    
    elif draw_type == UIDrawType.DTMF_NOTIFY:
        cmd.text = text_data.decode('ascii', errors='replace')
    
    return cmd
