// Runs in an AudioContext created at 16 kHz. Batches float samples into 100 ms Int16 PCM frames.
// processorOptions.channels = 2 sends interleaved stereo (left, right), which Transcribe channel identification reads
// as ch_0 and ch_1; otherwise the input is mono.
class PcmWorklet extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.channels = options?.processorOptions?.channels === 2 ? 2 : 1;
    this.buf = new Int16Array(1600 * this.channels);
    this.n = 0;
    this.peak = 0; // over the whole 100 ms frame, not just the last 128-sample render quantum
  }
  process(inputs) {
    const input = inputs[0];
    if (!input || !input[0]) return true;
    const frames = input[0].length;
    for (let i = 0; i < frames; i++) {
      for (let c = 0; c < this.channels; c++) {
        // a channel the browser has not connected yet is silence
        const s = Math.max(-1, Math.min(1, input[c] ? input[c][i] : 0));
        this.peak = Math.max(this.peak, Math.abs(s));
        this.buf[this.n++] = s < 0 ? s * 0x8000 : s * 0x7fff;
      }
      if (this.n === this.buf.length) {
        this.port.postMessage({ pcm: this.buf.buffer, peak: this.peak }, [this.buf.buffer]);
        this.buf = new Int16Array(1600 * this.channels);
        this.n = 0;
        this.peak = 0;
      }
    }
    return true;
  }
}
registerProcessor("pcm-worklet", PcmWorklet);
