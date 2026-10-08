// Shared PCM tap used by BOTH the HOST mic and the REMOTE source (replay <audio> or live WebRTC track).
// Converts mono float input to PCM16 frames of ~20 ms at the context's ACTUAL sample rate.
// No network calls here: frames are posted to the main thread, which owns the bounded sender.
class PcmTap extends AudioWorkletProcessor {
  constructor() {
    super();
    this.frameLen = Math.round(sampleRate * 0.02);
    this.buf = new Int16Array(this.frameLen);
    this.fill = 0;
    this.frameStart = 0;
    this.peak = 0;
  }
  process(inputs) {
    const input = inputs[0];
    if (!input || input.length === 0) return true;
    const ch0 = input[0];
    const ch1 = input.length > 1 ? input[1] : null;
    for (let i = 0; i < ch0.length; i++) {
      if (this.fill === 0) this.frameStart = currentTime + i / sampleRate;
      let s = ch1 ? (ch0[i] + ch1[i]) * 0.5 : ch0[i];
      if (s > 1) s = 1; else if (s < -1) s = -1;
      const a = s < 0 ? -s : s;
      if (a > this.peak) this.peak = a;
      this.buf[this.fill++] = s < 0 ? s * 0x8000 : s * 0x7fff;
      if (this.fill === this.frameLen) {
        const out = this.buf.slice(0);
        this.port.postMessage({ pcm: out.buffer, t: this.frameStart, peak: this.peak }, [out.buffer]);
        this.fill = 0;
        this.peak = 0;
      }
    }
    return true;
  }
}
registerProcessor('pcm-tap', PcmTap);
