/**
 * Q-Remote V3 – LCD Display Renderer
 * Renders UI packets from V1's parser format.
 */

const DISPLAY_W = 128;
const DISPLAY_H = 64;

export class DisplayRenderer {
    constructor(canvas) {
        this.canvas = canvas;
        this.ctx = canvas.getContext('2d');
        this.canvas.width = DISPLAY_W;
        this.canvas.height = DISPLAY_H;
        this.bgColor = '#0a0f0a';
        this.fgColor = '#00ff41';
        this.clear();
    }

    clear() {
        this.ctx.fillStyle = this.bgColor;
        this.ctx.fillRect(0, 0, DISPLAY_W, DISPLAY_H);
    }

    /**
     * Process UI event from SocketIO.
     * Format: {type, val1, val2, val3, dataLen, data}
     */
    processEvent(evt) {
        const {type, val1, val2, val3, dataLen, data} = evt;
        const bytes = data ? new Uint8Array(data) : null;

        switch (type) {
            case 0: // Large bold text
                this._drawText(bytes, val1, val2, 12, true);
                break;
            case 1: // Normal text (variable size)
                this._drawText(bytes, val1, val2, 7, false);
                break;
            case 2: // Inverted/bold text
                this._drawTextInverted(bytes, val1, val2, 7);
                break;
            case 3: // Medium bold text
                this._drawText(bytes, val1, val2, 9, true);
                break;
            case 5: // Clear lines (val1..val2)
                this.ctx.fillStyle = this.bgColor;
                const y1 = val1 * 8;
                const y2 = (val2 + 1) * 8;
                this.ctx.fillRect(0, y1, DISPLAY_W, y2 - y1);
                break;
            case 6: // Clear screen + register dump (ignore register data)
                this.clear();
                break;
            case 7: // Filled rectangle
                this.ctx.fillStyle = this.fgColor;
                this.ctx.fillRect(val1, val2, val3, bytes ? bytes[0] : 0);
                break;
            case 8: // Rectangle outline
                this.ctx.strokeStyle = this.fgColor;
                this.ctx.strokeRect(val1, val2, val3, bytes ? bytes[0] : 0);
                break;
        }
    }

    _drawText(bytes, x, line, fontSize, bold) {
        if (!bytes) return;
        const text = this._decode(bytes);
        const px = x;
        const py = line * 8;
        this.ctx.fillStyle = this.fgColor;
        this.ctx.font = `${bold ? 'bold ' : ''}${fontSize}px monospace`;
        this.ctx.textBaseline = 'top';
        this.ctx.fillText(text, px, py);
    }

    _drawTextInverted(bytes, x, line, fontSize) {
        if (!bytes) return;
        const text = this._decode(bytes);
        const px = x;
        const py = line * 8;
        const textWidth = text.length * (fontSize <= 7 ? 5 : 7);
        // Draw background
        this.ctx.fillStyle = this.fgColor;
        this.ctx.fillRect(px, py, textWidth, fontSize);
        // Draw text in background color
        this.ctx.fillStyle = this.bgColor;
        this.ctx.font = `${fontSize}px monospace`;
        this.ctx.textBaseline = 'top';
        this.ctx.fillText(text, px, py);
    }

    _decode(bytes) {
        let str = '';
        for (let i = 0; i < bytes.length; i++) {
            const b = bytes[i];
            if (b === 0) break;
            if (b >= 32 && b <= 126) {
                str += String.fromCharCode(b);
            } else {
                str += '?';
            }
        }
        return str;
    }
}
