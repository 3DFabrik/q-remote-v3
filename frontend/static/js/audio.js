/**
 * Q-Remote V3 - RX Audio Module (ulaw + ScriptProcessor)
 * Client-side RMS noise gate with per-sample attack/release.
 */

export class RxAudio {
    constructor() {
        this.ws = null;
        this.audioCtx = null;
        this.processor = null;
        this.connected = false;
        this.muted = false;
        this._pcmBuffer = [];
        this._maxBufferSamples = 1600; // ~200 ms at 8 kHz
        this._reconnectTimer = null;
        this._gatePollTimer = null;
        this.onConnectionChange = null;

        this._gateEnabled = true;
        this._gateThreshold = 300 / 32768;
        this._holdMs = 1000;
        this._attackMs = 2000;
        this._releaseMs = 2000;
        this._holdSamplesTotal = 8000;
        this._holdSamples = 0;
        this._gateGain = 0;
        this._attackStep = 1 / 280;
        this._releaseStep = 1 / 200;

        this._ulawTable = new Float32Array(256);
        for (let i = 0; i < 256; i++) {
            let u = ~i & 0xFF;
            let t = ((u & 0x0F) << 3) + 0x84;
            t <<= (u >> 4) & 0x07;
            const pcm = (u & 0x80) ? (0x84 - t) : (t - 0x84);
            this._ulawTable[i] = pcm / 32768.0;
        }
    }

    _updateRampSteps() {
        const rate = this.audioCtx ? this.audioCtx.sampleRate : 8000;
        const attackSamples = Math.max(1, (this._attackMs / 1000) * rate);
        const releaseSamples = Math.max(1, (this._releaseMs / 1000) * rate);
        this._attackStep = 1 / attackSamples;
        this._releaseStep = 1 / releaseSamples;
        this._holdSamplesTotal = Math.max(1, Math.round((this._holdMs / 1000) * rate));
    }

    applyGateConfig({ enabled, threshold, hold_ms, attack_ms, release_ms }) {
        this._gateEnabled = !!enabled;
        const t = Number(threshold);
        this._gateThreshold = (Number.isFinite(t) ? Math.max(50, Math.min(5000, t)) : 300) / 32768;
        const holdMs = Number(hold_ms);
        this._holdMs = Number.isFinite(holdMs) ? Math.max(50, Math.min(3000, holdMs)) : 1000;
        const attackMs = Number(attack_ms);
        this._attackMs = Number.isFinite(attackMs) ? Math.max(5, Math.min(3000, attackMs)) : 2000;
        const releaseMs = Number(release_ms);
        this._releaseMs = Number.isFinite(releaseMs) ? Math.max(5, Math.min(3000, releaseMs)) : 2000;
        this._updateRampSteps();
        if (!this._gateEnabled) {
            this._gateGain = 1;
            this._holdSamples = 0;
        }
    }

    async loadGateConfig() {
        try {
            const resp = await fetch("/api/audio/rx-gate");
            if (!resp.ok) return;
            this.applyGateConfig(await resp.json());
        } catch (e) {
            console.warn("[RxAudio] gate config load failed:", e);
        }
    }

    startGateConfigPoll(intervalMs = 30000) {
        if (this._gatePollTimer) clearInterval(this._gatePollTimer);
        this._gatePollTimer = setInterval(() => this.loadGateConfig(), intervalMs);
    }

    async start() {
        await this.loadGateConfig();
        this.startGateConfigPoll();

        try {
            this.audioCtx = new (window.AudioContext || window.webkitAudioContext)({
                sampleRate: 8000,
            });
            console.log("[RxAudio] AudioContext sampleRate:", this.audioCtx.sampleRate);
            this.applyGateConfig({
                enabled: this._gateEnabled,
                threshold: Math.round(this._gateThreshold * 32768),
                hold_ms: this._holdMs,
                attack_ms: this._attackMs,
                release_ms: this._releaseMs,
            });

            this.processor = this.audioCtx.createScriptProcessor(1024, 1, 1);
            this.processor.onaudioprocess = (e) => {
                const output = e.outputBuffer.getChannelData(0);
                const n = output.length;

                if (!this._gateEnabled || this.muted) {
                    for (let i = 0; i < n; i++) {
                        output[i] = this._pcmBuffer.length > 0 ? this._pcmBuffer.shift() : 0;
                    }
                    return;
                }

                let sumSq = 0;
                for (let i = 0; i < n; i++) {
                    const s = this._pcmBuffer.length > 0 ? this._pcmBuffer.shift() : 0;
                    sumSq += s * s;
                    output[i] = s;
                }

                const rms = Math.sqrt(sumSq / n);
                let wantOpen = false;
                if (rms > this._gateThreshold) {
                    wantOpen = true;
                    this._holdSamples = this._holdSamplesTotal;
                } else if (this._holdSamples > 0) {
                    wantOpen = true;
                    this._holdSamples = Math.max(0, this._holdSamples - n);
                }

                for (let i = 0; i < n; i++) {
                    if (wantOpen) {
                        this._gateGain = Math.min(1, this._gateGain + this._attackStep);
                    } else {
                        this._gateGain = Math.max(0, this._gateGain - this._releaseStep);
                    }
                    output[i] *= this._gateGain;
                }
            };
            this.processor.connect(this.audioCtx.destination);

            this._connectWS();
        } catch (e) {
            console.error("[RxAudio] Failed to start:", e);
            this._reconnectTimer = setTimeout(() => this.start(), 2000);
        }
    }

    _connectWS() {
        const protocol = location.protocol === "https:" ? "wss:" : "ws:";
        const wsUrl = protocol + "//" + location.host + "/audio/rx";
        console.log("[RxAudio] Connecting to", wsUrl);

        this.ws = new WebSocket(wsUrl);
        this.ws.binaryType = "arraybuffer";

        this.ws.onopen = () => {
            this.connected = true;
            console.log("[RxAudio] WebSocket connected");
            if (this.onConnectionChange) this.onConnectionChange(true);
            if (this.audioCtx && this.audioCtx.state === "suspended") {
                this.audioCtx.resume();
            }
        };

        this.ws.onmessage = (event) => {
            if (this.muted) return;
            const bytes = new Uint8Array(event.data);
            for (let i = 0; i < bytes.length; i++) {
                this._pcmBuffer.push(this._ulawTable[bytes[i]]);
            }
            while (this._pcmBuffer.length > this._maxBufferSamples) {
                this._pcmBuffer.shift();
            }
        };

        this.ws.onclose = () => {
            this.connected = false;
            if (this.onConnectionChange) this.onConnectionChange(false);
            console.log("[RxAudio] WebSocket closed, reconnecting...");
            this._reconnectTimer = setTimeout(() => this._connectWS(), 1000);
        };

        this.ws.onerror = (e) => {
            console.error("[RxAudio] WebSocket error:", e);
        };
    }

    flushBuffer() {
        this._pcmBuffer.length = 0;
    }

    stop() {
        if (this._gatePollTimer) {
            clearInterval(this._gatePollTimer);
            this._gatePollTimer = null;
        }
        if (this._reconnectTimer) {
            clearTimeout(this._reconnectTimer);
            this._reconnectTimer = null;
        }
        if (this.ws) {
            this.ws.onclose = null;
            this.ws.close();
            this.ws = null;
        }
        if (this.processor) {
            this.processor.disconnect();
            this.processor = null;
        }
        if (this.audioCtx) {
            this.audioCtx.close();
            this.audioCtx = null;
        }
        this.connected = false;
        if (this.onConnectionChange) this.onConnectionChange(false);
    }

    toggleMute() {
        this.muted = !this.muted;
        return this.muted;
    }
}
