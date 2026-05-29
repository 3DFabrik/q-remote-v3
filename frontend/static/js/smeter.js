/**
 * Q-Remote V3 – Analog S-Meter with TX Power scale
 * Dual-scale instrument: RX signal strength + TX output power
 */

export class AnalogSMeter {
    constructor(canvas) {
        this.canvas = canvas;
        this.ctx = canvas.getContext('2d');
        this.canvas.width = 260;
        this.canvas.height = 80;

        // Needle state
        this.currentAngle = 0;     // 0 = leftmost
        this.targetAngle = 0;
        this.decayTimer = null;
        this.isTX = false;

        // Colors
        this.bgColor = '#1a1a18';
        this.scaleColor = '#c8c0a0';
        this.needleColor = '#cc2200';
        this.accentColor = '#ffaa00';
        this.greenZone = '#00aa44';

        this.draw();
    }

    /**
     * Update RX signal strength.
     */
    updateRX(dbm, sUnit) {
        if (this.isTX) return;
        // Map -160 to 0 dBm → 0 to 1
        const normalized = Math.max(0, Math.min(1, (dbm + 160) / 130));
        this.targetAngle = normalized;
        this.animate();
    }

    /**
     * Switch to TX mode. Shows power level.
     */
    setTX(powerLevel) {
        this.isTX = true;
        // L=0.15, M=0.45, H=0.85
        const levels = { 'L': 0.15, 'M': 0.45, 'H': 0.85 };
        this.targetAngle = levels[powerLevel] || 0.5;
        this.animate();
    }

    /**
     * Switch back to RX mode.
     */
    setRX(dbm) {
        this.isTX = false;
        const normalized = Math.max(0, Math.min(1, (dbm + 160) / 130));
        this.targetAngle = normalized;
        this.animate();
    }

    animate() {
        if (this.decayTimer) return;
        const step = () => {
            const diff = this.targetAngle - this.currentAngle;
            if (Math.abs(diff) < 0.003) {
                this.currentAngle = this.targetAngle;
                this.draw();
                this.decayTimer = null;
                return;
            }
            const speed = diff > 0 ? 0.12 : 0.03;
            this.currentAngle += diff * speed;
            this.draw();
            this.decayTimer = requestAnimationFrame(step);
        };
        this.decayTimer = requestAnimationFrame(step);
    }

    draw() {
        const ctx = this.ctx;
        const w = this.canvas.width;
        const h = this.canvas.height;
        ctx.clearRect(0, 0, w, h);

        // Background
        ctx.fillStyle = this.bgColor;
        ctx.fillRect(0, 0, w, h);

        // Pivot below canvas (hidden mechanism)
        const cx = w / 2;
        const cy = h + 35;
        const radius = h + 28;

        // Arc sweep: from ~210° to ~330° (120° sweep centered on top)
        const sweepAngle = Math.PI * 0.72;  // 130° total sweep
        const startAngle = Math.PI * 1.5 - sweepAngle / 2;  // ~215°
        const endAngle = Math.PI * 1.5 + sweepAngle / 2;    // ~325°

        // ─── Scale ──────────────────────────────────────────
        // Main arc
        ctx.beginPath();
        ctx.arc(cx, cy, radius, startAngle, endAngle);
        ctx.lineWidth = 2;
        ctx.strokeStyle = this.scaleColor;
        ctx.stroke();

        // Inner arc (decorative)
        ctx.beginPath();
        ctx.arc(cx, cy, radius - 20, startAngle, endAngle);
        ctx.lineWidth = 0.5;
        ctx.strokeStyle = this.scaleColor + '40';
        ctx.stroke();

        if (this.isTX) {
            this._drawTXScale(ctx, cx, cy, radius, startAngle, endAngle);
        } else {
            this._drawRXScale(ctx, cx, cy, radius, startAngle, endAngle);
        }

        // ─── Needle ─────────────────────────────────────────
        const needleAngle = startAngle + this.currentAngle * (endAngle - startAngle);
        const needleLen = radius - 4;

        const nx = cx + Math.cos(needleAngle) * needleLen;
        const ny = cy + Math.sin(needleAngle) * needleLen;

        // Needle shadow
        ctx.beginPath();
        ctx.moveTo(cx + 1, cy + 1);
        ctx.lineTo(nx + 1, ny + 1);
        ctx.lineWidth = 2.5;
        ctx.strokeStyle = 'rgba(0,0,0,0.3)';
        ctx.stroke();

        // Needle
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.lineTo(nx, ny);
        ctx.lineWidth = 2;
        ctx.strokeStyle = this.needleColor;
        ctx.stroke();

        // Needle tip glow
        ctx.beginPath();
        ctx.arc(nx, ny, 2, 0, Math.PI * 2);
        ctx.fillStyle = this.needleColor;
        ctx.fill();
    }

    _drawRXScale(ctx, cx, cy, radius, startAngle, endAngle) {
        const totalSweep = endAngle - startAngle;

        // S-unit labels
        const labels = ['1', '3', '5', '7', '9', '+20', '+40', '+60'];

        // S-unit zones: slight green tint from S7 up
        const s7Frac = 6 / 9;
        const s7Angle = startAngle + s7Frac * totalSweep;
        ctx.beginPath();
        ctx.arc(cx, cy, radius - 1, s7Angle, endAngle);
        ctx.lineWidth = 4;
        ctx.strokeStyle = this.greenZone + '30';
        ctx.stroke();

        // Main ticks and labels (S1 through S9+60)
        for (let i = 0; i <= 9; i++) {
            const frac = i / 9;
            const angle = startAngle + frac * (totalSweep * 9 / 10);  // S1-S9 use 90% of arc

            const isMajor = i % 2 === 0;
            const tickLen = isMajor ? 12 : 7;

            const x1 = cx + Math.cos(angle) * (radius - tickLen);
            const y1 = cy + Math.sin(angle) * (radius - tickLen);
            const x2 = cx + Math.cos(angle) * radius;
            const y2 = cy + Math.sin(angle) * radius;

            ctx.beginPath();
            ctx.moveTo(x1, y1);
            ctx.lineTo(x2, y2);
            ctx.lineWidth = isMajor ? 1.5 : 0.8;
            ctx.strokeStyle = this.scaleColor;
            ctx.stroke();

            // S-unit label
            if (i % 2 === 0) {
                const labelR = radius - 22;
                const lx = cx + Math.cos(angle) * labelR;
                const ly = cy + Math.sin(angle) * labelR;
                ctx.fillStyle = i >= 7 ? this.greenZone : this.scaleColor;
                ctx.font = 'bold 10px monospace';
                ctx.textAlign = 'center';
                ctx.textBaseline = 'middle';
                ctx.fillText(`S${labels[i / 2]}`, lx, ly);
            }
        }

        // +dB over S9 ticks
        const overLabels = ['+20', '+40', '+60'];
        const s9Angle = startAngle + (9 / 9) * (totalSweep * 9 / 10);
        const overSweep = totalSweep * 1 / 10;
        for (let i = 0; i <= 6; i++) {
            const frac = i / 6;
            const angle = s9Angle + frac * overSweep;
            const isMajor = i % 2 === 0;
            const tickLen = isMajor ? 12 : 7;

            const x1 = cx + Math.cos(angle) * (radius - tickLen);
            const y1 = cy + Math.sin(angle) * (radius - tickLen);
            const x2 = cx + Math.cos(angle) * radius;
            const y2 = cy + Math.sin(angle) * radius;

            ctx.beginPath();
            ctx.moveTo(x1, y1);
            ctx.lineTo(x2, y2);
            ctx.lineWidth = isMajor ? 1.5 : 0.8;
            ctx.strokeStyle = this.accentColor;
            ctx.stroke();

            if (isMajor && i > 0) {
                const labelR = radius - 22;
                const lx = cx + Math.cos(angle) * labelR;
                const ly = cy + Math.sin(angle) * labelR;
                ctx.fillStyle = this.accentColor;
                ctx.font = 'bold 9px monospace';
                ctx.textAlign = 'center';
                ctx.textBaseline = 'middle';
                ctx.fillText(overLabels[(i / 2) - 1] || '', lx, ly);
            }
        }

        // Mode label
        ctx.fillStyle = this.scaleColor + '80';
        ctx.font = '9px monospace';
        ctx.textAlign = 'center';
        ctx.fillText('RX', cx, cy - radius + 42);
    }

    _drawTXScale(ctx, cx, cy, radius, startAngle, endAngle) {
        const totalSweep = endAngle - startAngle;

        // TX power levels: L, M, H with color zones
        const zones = [
            { label: 'LOW', frac: 0.25, color: '#00aa44' },
            { label: 'MED', frac: 0.55, color: '#ffaa00' },
            { label: 'HIGH', frac: 0.85, color: '#cc2200' },
        ];

        // Color zones
        for (const zone of zones) {
            const angle = startAngle + zone.frac * totalSweep;
            ctx.beginPath();
            ctx.arc(cx, cy, radius - 1, angle - 0.15, angle + 0.15);
            ctx.lineWidth = 5;
            ctx.strokeStyle = zone.color + '50';
            ctx.stroke();
        }

        // Tick marks for L, M, H
        for (const zone of zones) {
            const angle = startAngle + zone.frac * totalSweep;

            const x1 = cx + Math.cos(angle) * (radius - 14);
            const y1 = cy + Math.sin(angle) * (radius - 14);
            const x2 = cx + Math.cos(angle) * radius;
            const y2 = cy + Math.sin(angle) * radius;

            ctx.beginPath();
            ctx.moveTo(x1, y1);
            ctx.lineTo(x2, y2);
            ctx.lineWidth = 2;
            ctx.strokeStyle = zone.color;
            ctx.stroke();

            // Label
            const labelR = radius - 24;
            const lx = cx + Math.cos(angle) * labelR;
            const ly = cy + Math.sin(angle) * labelR;
            ctx.fillStyle = zone.color;
            ctx.font = 'bold 10px monospace';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(zone.label, lx, ly);
        }

        // Small ticks between
        for (let i = 0; i <= 12; i++) {
            const frac = i / 12;
            const angle = startAngle + frac * totalSweep;
            const x1 = cx + Math.cos(angle) * (radius - 5);
            const y1 = cy + Math.sin(angle) * (radius - 5);
            const x2 = cx + Math.cos(angle) * radius;
            const y2 = cy + Math.sin(angle) * radius;
            ctx.beginPath();
            ctx.moveTo(x1, y1);
            ctx.lineTo(x2, y2);
            ctx.lineWidth = 0.5;
            ctx.strokeStyle = this.scaleColor + '40';
            ctx.stroke();
        }

        // Mode label
        ctx.fillStyle = '#cc2200';
        ctx.font = 'bold 10px monospace';
        ctx.textAlign = 'center';
        ctx.fillText('TX', cx, cy - radius + 42);
    }
}
