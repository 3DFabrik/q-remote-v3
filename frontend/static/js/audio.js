/**
 * Q-Remote V3 - RX Audio Module (ulaw + ScriptProcessor)
 * Proven working on Android/iOS with user-gesture start.
 */

export class RxAudio {
    constructor() {
        this.ws = null;
        this.audioCtx = null;
        this.processor = null;
        this.connected = false;
        this.muted = false;
        this._pcmBuffer = [];
        this._maxBufferSamples = 960; // ~120 ms at 8 kHz — low latency
        this._reconnectTimer = null;
        this.onConnectionChange = null;  // (connected: boolean) => {}

        // ulaw decode table
        this._ulawTable = new Float32Array(256);
        for (let i = 0; i < 256; i++) {
            let u = ~i & 0xFF;
            let t = ((u & 0x0F) << 3) + 0x84;
            t <<= (u >> 4) & 0x07;
            const pcm = (u & 0x80) ? (0x84 - t) : (t - 0x84);
            this._ulawTable[i] = pcm / 32768.0;
        }
    }

    async start() {
        try {
            this.audioCtx = new (window.AudioContext || window.webkitAudioContext)({
                sampleRate: 8000,
            });
            console.log("[RxAudio] AudioContext sampleRate:", this.audioCtx.sampleRate);

            this.processor = this.audioCtx.createScriptProcessor(1024, 1, 1);
            this.processor.onaudioprocess = (e) => {
                const output = e.outputBuffer.getChannelData(0);
                for (let i = 0; i < output.length; i++) {
                    output[i] = this._pcmBuffer.length > 0 ? this._pcmBuffer.shift() : 0;
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
