// AudioWorklet: converts the (already 16 kHz) microphone stream into 100 ms frames of 16-bit LE PCM
// and posts them to the main thread. Plain JS in public/ so it loads under the 'self'-only CSP.
const FRAME_SAMPLES = 1600

class PcmCapture extends AudioWorkletProcessor {
  constructor() {
    super()
    this.buf = new Int16Array(FRAME_SAMPLES)
    this.len = 0
  }

  process(inputs) {
    const ch = inputs[0] && inputs[0][0]
    if (!ch) return true
    for (let i = 0; i < ch.length; i++) {
      const v = Math.max(-1, Math.min(1, ch[i]))
      this.buf[this.len++] = v < 0 ? v * 0x8000 : v * 0x7fff
      if (this.len === FRAME_SAMPLES) {
        const out = this.buf.buffer
        this.port.postMessage(out, [out])
        this.buf = new Int16Array(FRAME_SAMPLES)
        this.len = 0
      }
    }
    return true
  }
}

registerProcessor('pcm-capture', PcmCapture)
