/**
 * AudioWorklet Processor: Raw 16-bit PCM at 8kHz → direct output
 * No resampling. AudioContext must be set to 8000 Hz.
 */

class UlamProcessor extends AudioWorkletProcessor {
    constructor() {
        super();
        this._buf = new Float32Array(16000);
        this._wp = 0;
        this._rp = 0;
        this._avail = 0;

        this.port.onmessage = (e) => {
            const raw = new Int16Array(e.data);
            for (let i = 0; i < raw.length; i++) {
                if (this._avail >= this._buf.length) {
                    this._rp = (this._rp + 1) % this._buf.length;
                    this._avail--;
                }
                this._buf[this._wp] = raw[i] / 32768.0;
                this._wp = (this._wp + 1) % this._buf.length;
                this._avail++;
            }
        };
    }

    process(inputs, outputs, parameters) {
        const out = outputs[0];
        if (!out || !out[0]) return true;
        const ch = out[0];

        for (let i = 0; i < ch.length; i++) {
            if (this._avail > 0) {
                ch[i] = this._buf[this._rp];
                this._rp = (this._rp + 1) % this._buf.length;
                this._avail--;
            } else {
                ch[i] = 0;
            }
        }
        return true;
    }
}

// Keep the name for compatibility
registerProcessor('ulaw-processor', UlamProcessor);
