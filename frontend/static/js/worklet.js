/**
 * AudioWorklet Processor: ulaw bytes at 8kHz -> Float32 output
 */

class UlawProcessor extends AudioWorkletProcessor {
    constructor() {
        super();
        this._buf = new Float32Array(16000);
        this._wp = 0;
        this._rp = 0;
        this._avail = 0;

        // Build ulaw decode table
        this._ulawTable = new Float32Array(256);
        for (let i = 0; i < 256; i++) {
            let u = ~i & 0xFF;
            let t = ((u & 0x0F) << 3) + 0x84;
            t <<= (u >> 4) & 0x07;
            const pcm = (u & 0x80) ? (0x84 - t) : (t - 0x84);
            this._ulawTable[i] = pcm / 32768.0;
        }

        this.port.onmessage = (e) => {
            const bytes = new Uint8Array(e.data);
            for (let i = 0; i < bytes.length; i++) {
                if (this._avail >= this._buf.length) {
                    this._rp = (this._rp + 1) % this._buf.length;
                    this._avail--;
                }
                this._buf[this._wp] = this._ulawTable[bytes[i]];
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

registerProcessor("ulaw-processor", UlawProcessor);
