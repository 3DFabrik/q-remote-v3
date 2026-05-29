/**
 * Q-Remote V3 – LCD Display Renderer
 * Renders V1's lcd_update events (fragment-based text positioning).
 */

const SCALE = 3;
const DISPLAY_W = 128 * SCALE;
const DISPLAY_H = 64 * SCALE;

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
     * Process lcd_update event from V1's LCDDisplay.
     * Format: {fragments: {0: [...], 1: [...], ...}, smeter, state, battery_v, ...}
     */
    processEvent(state) {
        // Don't clear – paint over previous frame to avoid flicker
        this.ctx.fillStyle = this.bgColor;
        this.ctx.fillRect(0, 0, DISPLAY_W, DISPLAY_H);

        const {fragments} = state;

        for (let lineIdx = 0; lineIdx < 8; lineIdx++) {
            const lineFrags = fragments[String(lineIdx)];
            if (!lineFrags || lineFrags.length === 0) continue;

            const py = lineIdx * 8 * SCALE;

            for (const frag of lineFrags) {
                const px = frag.x * SCALE;
                const fontSize = Math.max(5, Math.round(frag.size * 6 * SCALE));
                const text = frag.text;
                if (!text) continue;

                // All text positive (green on dark) for readability
                this.ctx.fillStyle = this.fgColor;

                this.ctx.font = `${frag.bold ? 'bold ' : ''}${fontSize}px monospace`;
                this.ctx.textBaseline = 'top';
                this.ctx.fillText(text, px, py);
            }
        }
    }
}
