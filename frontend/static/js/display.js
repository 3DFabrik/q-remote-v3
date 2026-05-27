/**
 * Q-Remote V3 – LCD Display Renderer
 * 
 * Renders the Quansheng UV-K5 LCD on an HTML canvas.
 * Receives 0xB5 UI rendering commands from the radio and draws them.
 * 
 * UV-K5 display: 128x64 pixels, monochrome with inverted regions.
 * Rendered in green phosphor style to match the Mission Control theme.
 */

const DISPLAY_W = 128;
const DISPLAY_H = 64;

// Font metrics (approximate for the UV-K5's built-in font)
const FONT_LARGE_H = 12;    // Large bold text
const FONT_MEDIUM_H = 9;    // Medium bold text
const FONT_SMALL_H = 7;     // Normal text

export class DisplayRenderer {
    constructor(canvas) {
        this.canvas = canvas;
        this.ctx = canvas.getContext('2d');
        this.canvas.width = DISPLAY_W;
        this.canvas.height = DISPLAY_H;
        
        // Colors (green phosphor CRT)
        this.bgColor = '#0a0f0a';
        this.fgColor = '#00ff41';
        this.fgDimColor = '#00aa2a';
        this.glowColor = 'rgba(0, 255, 65, 0.3)';
        
        this.clear();
    }
    
    clear() {
        this.ctx.fillStyle = this.bgColor;
        this.ctx.fillRect(0, 0, DISPLAY_W, DISPLAY_H);
    }
    
    /**
     * Process a UI rendering command from the radio.
     * Data format: [type, val1, val2, val3, dataLen, ...dataBytes]
     */
    processCommand(rawData) {
        if (!rawData || rawData.length < 6) return;
        
        const data = rawData instanceof Uint8Array ? rawData : new Uint8Array(rawData);
        const type = data[0];
        const val1 = data[1];
        const val2 = data[2];
        const val3 = data[3];
        const dataLen = data[4];
        const textData = data.slice(5, 5 + dataLen);
        
        switch (type) {
            case 0: this._drawText(textData, val1, val2, FONT_LARGE_H, true, false); break;
            case 1: this._drawText(textData, val1, val2, FONT_SMALL_H, false, false); break;
            case 2: this._drawText(textData, val1, val2, FONT_SMALL_H, false, true); break;
            case 3: this._drawText(textData, val1, val2, FONT_MEDIUM_H, true, false); break;
            case 5: this._clearLines(val1, val2); break;
            case 6: this.clear(); break;
            case 7: this._drawRect(val1, val2, val3, textData[0] || 0, true); break;
            case 8: this._drawRect(val1, val2, val3, textData[0] || 0, false); break;
        }
    }
    
    /**
     * Process a batch of UI commands.
     */
    processBatch(commands) {
        for (const cmd of commands) {
            this.processCommand(cmd);
        }
    }
    
    // ─── Drawing Primitives ────────────────────────────────────
    
    _drawText(textBytes, x, y, fontSize, bold, inverted) {
        const text = this._decodeText(textBytes);
        
        // Pixel coordinates: val1 is column, val2 is line/row
        const px = x;
        const py = y * 8; // Lines are 8 pixels tall
        
        if (inverted) {
            // Draw background then text
            const textWidth = text.length * (fontSize <= FONT_SMALL_H ? 5 : 7);
            this.ctx.fillStyle = this.fgColor;
            this.ctx.fillRect(px, py, textWidth, fontSize);
            this.ctx.fillStyle = this.bgColor;
        } else {
            this.ctx.fillStyle = this.fgColor;
        }
        
        this.ctx.font = `${fontSize}px monospace`;
        this.ctx.textBaseline = 'top';
        this.ctx.fillText(text, px, py);
    }
    
    _clearLines(fromLine, toLine) {
        this.ctx.fillStyle = this.bgColor;
        this.ctx.fillRect(0, fromLine * 8, DISPLAY_W, (toLine - fromLine + 1) * 8);
    }
    
    _drawRect(x, y, w, h, filled) {
        if (filled) {
            this.ctx.fillStyle = this.fgColor;
            this.ctx.fillRect(x, y, w, h);
        } else {
            this.ctx.strokeStyle = this.fgColor;
            this.ctx.lineWidth = 1;
            this.ctx.strokeRect(x + 0.5, y + 0.5, w - 1, h - 1);
        }
    }
    
    _decodeText(bytes) {
        let str = '';
        for (let i = 0; i < bytes.length; i++) {
            const b = bytes[i];
            if (b === 0) break;
            // Map some special chars from UV-K5 encoding
            if (b >= 32 && b <= 126) {
                str += String.fromCharCode(b);
            } else if (b === 0x7F) {
                str += '→';
            } else {
                str += '?';
            }
        }
        return str;
    }
}
