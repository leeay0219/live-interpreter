// Runs in an AudioContext created at 16 kHz. Batches mono float samples into 100 ms Int16 PCM frames.
class PcmWorklet extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buf = new Int16Array(1600);
    this.n = 0;
    this.peak = 0; // over the whole 100 ms frame, not just the last 128-sample render quantum
  }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) {
      const s = Math.max(-1, Math.min(1, ch[i]));
      this.peak = Math.max(this.peak, Math.abs(s));
      this.buf[this.n++] = s < 0 ? s * 0x8000 : s * 0x7fff;
      if (this.n === this.buf.length) {
        this.port.postMessage({ pcm: this.buf.buffer, peak: this.peak }, [this.buf.buffer]);
        this.buf = new Int16Array(1600);
        this.n = 0;
        this.peak = 0;
      }
    }
    return true;
  }
}
registerProcessor("pcm-worklet", PcmWorklet);
