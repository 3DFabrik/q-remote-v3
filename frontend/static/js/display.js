/**
 * Q-Remote V3 – LCD Display Renderer
 * Renders V1's lcd_update events (fragment-based text positioning).
 */

const SCALE = 3;
const DISPLAY_W = 128 * SCALE;
const DISPLAY_H = 64 * SCALE;
const WARMUP_MS = 3200;
const REDUCED_MOTION = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

export class DisplayRenderer {
    constructor(canvas) {
        this.canvas = canvas;
        this.ctx = canvas.getContext('2d');
        this.canvas.width = DISPLAY_W;
        this.canvas.height = DISPLAY_H;
        this.bgColor = '#0a0f0a';
        this.fgColor = '#00ff41';
        this._lastState = null;
        this._warmupArmed = !REDUCED_MOTION;
        this._warmupStart = 0;
        this._warmupDuration = WARMUP_MS;
        this._warmupRaf = null;
        this.clear();
    }

    clear() {
        this.ctx.fillStyle = this.bgColor;
        this.ctx.fillRect(0, 0, DISPLAY_W, DISPLAY_H);
    }

    _hasVisibleText(state) {
        const {fragments} = state;
        if (!fragments) return false;
        for (let lineIdx = 0; lineIdx < 8; lineIdx++) {
            const lineFrags = fragments[String(lineIdx)];
            if (!lineFrags) continue;
            for (const frag of lineFrags) {
                if (frag.text && String(frag.text).trim()) return true;
            }
        }
        return false;
    }

    _beginWarmup() {
        this._warmupStart = performance.now();
        const section = document.getElementById('display-section');
        if (section) section.classList.add('crt-warming');
        this._scheduleWarmupFrame();
    }

    _isWarming() {
        return this._warmupStart > 0
            && (performance.now() - this._warmupStart) < this._warmupDuration;
    }

    _phosphorLevel() {
        if (!this._warmupStart) return 1;
        const elapsed = performance.now() - this._warmupStart;
        if (elapsed >= this._warmupDuration) {
            this._endWarmup();
            return 1;
        }
        const t = elapsed / this._warmupDuration;
        // Slow CRT build-up: long dim phase, then phosphor catches up
        return 1 - Math.pow(1 - t, 3.2);
    }

    _endWarmup() {
        this._warmupStart = 0;
        if (this._warmupRaf) {
            cancelAnimationFrame(this._warmupRaf);
            this._warmupRaf = null;
        }
        const section = document.getElementById('display-section');
        if (section) section.classList.remove('crt-warming');
    }

    _scheduleWarmupFrame() {
        if (this._warmupRaf || !this._isWarming()) return;
        this._warmupRaf = requestAnimationFrame(() => {
            this._warmupRaf = null;
            if (!this._lastState) return;
            this._drawFrame(this._lastState);
            if (this._isWarming()) {
                this._scheduleWarmupFrame();
            } else {
                this._drawFrame(this._lastState);
            }
        });
    }

    /**
     * Process lcd_update event from V1's LCDDisplay.
     * Format: {fragments: {0: [...], 1: [...], ...}, smeter, state, battery_v, ...}
     */
    processEvent(state) {
        if (this._warmupArmed && this._hasVisibleText(state)) {
            this._warmupArmed = false;
            this._beginWarmup();
        }

        this._lastState = state;
        this._drawFrame(state);
        if (this._isWarming()) {
            this._scheduleWarmupFrame();
        }
    }

    _drawContent(state) {
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

                this.ctx.fillStyle = this.fgColor;
                this.ctx.font = `${frag.bold ? 'bold ' : ''}${fontSize}px monospace`;
                this.ctx.textBaseline = 'top';
                this.ctx.fillText(text, px, py);
            }
        }
    }

    _drawFrame(state) {
        this.ctx.fillStyle = this.bgColor;
        this.ctx.fillRect(0, 0, DISPLAY_W, DISPLAY_H);
        this._drawContent(state);

        const phosphor = this._phosphorLevel();
        if (phosphor < 1) {
            // Dark veil lifts slowly — mimics a CRT warming from black
            this.ctx.fillStyle = `rgba(10, 15, 10, ${1 - phosphor})`;
            this.ctx.fillRect(0, 0, DISPLAY_W, DISPLAY_H);
        }
    }
}
