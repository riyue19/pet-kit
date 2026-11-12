import { MIC_SAMPLE_RATE } from '../shared/agent'
import { PcmDecoder, shouldStartPlayback } from './pcm'

export { MouthFollower } from './pcm'

/** Small gap so the first scheduled buffer is never in the past. */
const SCHEDULE_LEAD_S = 0.05
const ANALYSER_SIZE = 512

/**
 * Microphone -> 16 kHz mono 16-bit PCM frames (100 ms each).
 * Chromium's echo cancellation uses this app's own audio output as reference, so the pet's voice is
 * mostly removed before it reaches the recognizer.
 */
export class MicCapture {
  private ctx: AudioContext | null = null
  private stream: MediaStream | null = null
  private node: AudioWorkletNode | null = null
  private starting: Promise<void> | null = null
  private wanted = false

  constructor(private readonly onFrame: (pcm: ArrayBuffer) => void) {}

  get running(): boolean {
    return this.ctx !== null
  }

  /** Idempotent; resolves once capture runs (or immediately if stop() was called meanwhile). */
  start(): Promise<void> {
    this.wanted = true
    if (this.ctx) return Promise.resolve()
    this.starting ??= this.open().finally(() => {
      this.starting = null
      if (!this.wanted) this.close()
    })
    return this.starting
  }

  stop(): void {
    this.wanted = false
    if (!this.starting) this.close()
  }

  private async open(): Promise<void> {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
      video: false
    })
    let ctx: AudioContext | null = null
    try {
      ctx = new AudioContext({ sampleRate: MIC_SAMPLE_RATE })
      await ctx.audioWorklet.addModule('./audio/mic-worklet.js')
      const source = ctx.createMediaStreamSource(stream)
      const node = new AudioWorkletNode(ctx, 'pcm-capture', { numberOfOutputs: 1, outputChannelCount: [1] })
      node.port.onmessage = (e: MessageEvent<ArrayBuffer>) => {
        if (this.wanted) this.onFrame(e.data)
      }
      // A muted path to the destination keeps the graph pulling the worklet.
      const mute = ctx.createGain()
      mute.gain.value = 0
      source.connect(node).connect(mute).connect(ctx.destination)
      this.stream = stream
      this.ctx = ctx
      this.node = node
    } catch (e) {
      stream.getTracks().forEach((t) => t.stop())
      void ctx?.close()
      throw e
    }
  }

  private close(): void {
    this.node?.port.close()
    this.node = null
    this.stream?.getTracks().forEach((t) => t.stop())
    this.stream = null
    void this.ctx?.close()
    this.ctx = null
  }
}

/** Plays streamed TTS PCM gaplessly and exposes an output level for lip sync. */
export class SpeechPlayer {
  private ctx: AudioContext | null = null
  private analyser: AnalyserNode | null = null
  private sources = new Set<AudioBufferSourceNode>()
  private nextTime = 0
  private id = -1
  private sampleRate = 24000
  private streamDone = true
  private playing = false
  private readonly decoder = new PcmDecoder()
  private pending: Float32Array<ArrayBuffer>[] = []
  private pendingSeconds = 0
  private readonly levelBuf = new Float32Array(ANALYSER_SIZE)

  constructor(private readonly onPlayingChange: (playing: boolean) => void) {}

  private ensureContext(): AudioContext {
    if (!this.ctx) {
      this.ctx = new AudioContext()
      this.analyser = this.ctx.createAnalyser()
      this.analyser.fftSize = ANALYSER_SIZE
      this.analyser.connect(this.ctx.destination)
    }
    if (this.ctx.state === 'suspended') void this.ctx.resume()
    return this.ctx
  }

  begin(id: number, sampleRate: number): void {
    this.stop()
    this.ensureContext()
    this.id = id
    this.sampleRate = sampleRate
    this.streamDone = false
    this.nextTime = 0
    this.pending = []
    this.pendingSeconds = 0
    this.decoder.reset()
  }

  push(chunk: Uint8Array): void {
    if (this.id < 0 || !this.ctx || !this.analyser) return
    const samples = this.decoder.decode(chunk)
    if (samples.length === 0) return
    this.pending.push(samples)
    this.pendingSeconds += samples.length / this.sampleRate
    this.flush()
  }

  end(id: number): void {
    if (id !== this.id) return
    this.streamDone = true
    this.flush()
    this.settle()
  }

  /**
   * MiniMax streams only slightly faster than real time and in bursts, so playing each chunk as it
   * arrives runs dry between bursts. Hold chunks until enough audio is queued (or the stream ended),
   * both at the start and after any underrun.
   */
  private flush(): void {
    const ctx = this.ctx
    if (!ctx || !this.analyser || this.pending.length === 0) return
    const ahead = Math.max(0, this.nextTime - ctx.currentTime)
    if (!shouldStartPlayback(ahead, this.pendingSeconds, this.streamDone, this.nextTime === 0)) return
    for (const samples of this.pending) {
      const buffer = ctx.createBuffer(1, samples.length, this.sampleRate)
      buffer.copyToChannel(samples, 0)
      const src = ctx.createBufferSource()
      src.buffer = buffer
      src.connect(this.analyser)
      const at = this.nextTime > ctx.currentTime ? this.nextTime : ctx.currentTime + SCHEDULE_LEAD_S
      src.start(at)
      this.nextTime = at + buffer.duration
      this.sources.add(src)
      src.onended = () => {
        this.sources.delete(src)
        this.settle()
      }
    }
    this.pending = []
    this.pendingSeconds = 0
    this.setPlaying(true)
  }

  /** Stops immediately (interrupt / new utterance). */
  stop(): void {
    for (const src of this.sources) {
      src.onended = null
      try {
        src.stop()
      } catch {
        // already stopped
      }
      src.disconnect()
    }
    this.sources.clear()
    this.pending = []
    this.pendingSeconds = 0
    this.id = -1
    this.streamDone = true
    this.nextTime = 0
    this.setPlaying(false)
  }

  /** RMS of what is audible right now, 0..~1. */
  level(): number {
    if (!this.playing || !this.analyser) return 0
    this.analyser.getFloatTimeDomainData(this.levelBuf)
    let sum = 0
    for (const v of this.levelBuf) sum += v * v
    return Math.sqrt(sum / this.levelBuf.length)
  }

  private settle(): void {
    if (this.streamDone && this.sources.size === 0 && this.pending.length === 0) this.setPlaying(false)
  }

  private setPlaying(playing: boolean): void {
    if (playing === this.playing) return
    this.playing = playing
    this.onPlayingChange(playing)
  }
}
