/** DOM-free audio helpers (unit-tested in tests/frontend/pcm.test.ts). */

/** 16-bit LE PCM bytes -> float samples, carrying a sample split across chunk boundaries. */
export class PcmDecoder {
  private carry: number | null = null

  reset(): void {
    this.carry = null
  }

  decode(chunk: Uint8Array): Float32Array<ArrayBuffer> {
    let bytes = chunk
    if (this.carry !== null) {
      bytes = new Uint8Array(chunk.length + 1)
      bytes[0] = this.carry
      bytes.set(chunk, 1)
    }
    const n = bytes.length >> 1
    this.carry = bytes.length % 2 ? bytes[bytes.length - 1] : null
    const view = new DataView(bytes.buffer, bytes.byteOffset, n * 2)
    const out = new Float32Array(n)
    for (let i = 0; i < n; i++) out[i] = view.getInt16(i * 2, true) / 32768
    return out
  }
}

/** Maps output RMS to a mouth opening with a little smoothing; call once per frame. */
export class MouthFollower {
  private value = 0

  next(rms: number): number {
    // Speech RMS sits around 0.03-0.3; below the floor the mouth closes.
    const target = Math.min(1, Math.max(0, (rms - 0.015) * 5))
    // Open fast, close a bit slower so syllables read clearly.
    const k = target > this.value ? 0.6 : 0.35
    this.value += (target - this.value) * k
    return this.value < 0.02 ? 0 : this.value
  }
}

/** Audio to queue before starting playback (first chunk, or after running dry). */
export const START_BUFFER_S = 1.2
/** While playing, keep this much scheduled ahead; with less, hold new chunks until START_BUFFER_S is queued. */
export const LOW_WATER_S = 0.15

/**
 * Whether queued chunks should be scheduled now. `ahead` = seconds already scheduled but not yet played.
 * Playing normally (ahead above the low-water mark) just appends. Starting or recovering from an
 * underrun waits for START_BUFFER_S, or for the stream to finish.
 */
export function shouldStartPlayback(ahead: number, pendingSeconds: number, streamDone: boolean, first: boolean): boolean {
  if (streamDone) return true
  if (!first && ahead > LOW_WATER_S) return true
  return pendingSeconds >= START_BUFFER_S
}
