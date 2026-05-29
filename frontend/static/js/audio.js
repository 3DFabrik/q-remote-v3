/**
 * Q-Remote V3 – RX Audio Module v2
 * Test: Use ScriptProcessorNode instead of AudioWorklet for debugging
 */

export class RxAudio {
    constructor() {
        this.ws = null;
        this.audioCtx = null;
        this.processor = null;
        this.connected = false;
        this.muted = false;
        this._pcmBuffer = [];
        this._reconnectTimer = null;

        // μ-law decode table
        this._ulawTable = new Float32Array(256);
        for (let i = 0; i < 256; i++) {
            let u = ~i & 0xFF;
            let t = ((u & 0x0F) << 3) + 0x84;
            t <<= (u >> 4) & 0x07;
            const pcm = (u & 0x80) ? (0x84 - t) : (t - 0x84);
            this._ulawTable[i] = pcm / 32768.0;
        }
    }

    _ulawDecode(byte) {
        return this._ulawTable[byte & 0xFF];
    }

    async start() {
        try {
            this.audioCtx = new (window.AudioContext || window.webkitAudioContext)({
                sampleRate: 8000,
            });
            console.log('[RxAudio] AudioContext sampleRate:', this.audioCtx.sampleRate);

            // Use ScriptProcessorNode (deprecated but reliable for testing)
            this.processor = this.audioCtx.createScriptProcessor(4096, 1, 1);
            
            this.processor.onaudioprocess = (e) => {
                const output = e.outputBuffer.getChannelData(0);
                const needed = output.length;
                
                for (let i = 0; i < needed; i++) {
                    if (this._pcmBuffer.length > 0) {
                        output[i] = this._pcmBuffer.shift();
                    } else {
                        output[i] = 0;
                    }
                }
            };

            this.processor.connect(this.audioCtx.destination);

            this._connectWS();
        } catch (e) {
            console.error('[RxAudio] Failed to start:', e);
            this._reconnectTimer = setTimeout(() => this.start(), 2000);
        }
    }

    _connectWS() {
        const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${location.host}/audio/rx`;
        console.log('[RxAudio] Connecting to', wsUrl);

        this.ws = new WebSocket(wsUrl);
        this.ws.binaryType = 'arraybuffer';

        this.ws.onopen = () => {
            this.connected = true;
            console.log('[RxAudio] WebSocket connected');
            if (this.audioCtx && this.audioCtx.state === 'suspended') {
                this.audioCtx.resume();
            }
        };

        this.ws.onmessage = (event) => {
            if (this.muted) return;
            // μ-law encoded bytes → decode to float
            const bytes = new Uint8Array(event.data);
            for (let i = 0; i < bytes.length; i++) {
                this._pcmBuffer.push(this._ulawDecode(bytes[i]));
            }
            // Prevent buffer from growing too large (max 2 seconds)
            while (this._pcmBuffer.length > 16000) {
                this._pcmBuffer.shift();
            }
        };

        this.ws.onclose = () => {
            this.connected = false;
            console.log('[RxAudio] WebSocket closed, reconnecting...');
            this._reconnectTimer = setTimeout(() => this._connectWS(), 1000);
        };

        this.ws.onerror = (e) => {
            console.error('[RxAudio] WebSocket error:', e);
        };
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
    }

    toggleMute() {
        this.muted = !this.muted;
        return this.muted;
    }
}
