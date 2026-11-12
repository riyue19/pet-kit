import { describe, expect, it } from 'vitest'
import { LOW_WATER_S, START_BUFFER_S, shouldStartPlayback } from '../../frontend/src/renderer/pcm'

/** Replays chunk arrivals through the buffering rule; returns total silence after playback started. */
function simulate(arrivals: [number, number][]): { start: number; gaps: number } {
  let pending = 0
  let nextTime = 0
  let start = -1
  let gaps = 0
  arrivals.forEach(([t, dur], i) => {
    pending += dur
    const done = i === arrivals.length - 1
    const ahead = Math.max(0, nextTime - t)
    if (!shouldStartPlayback(ahead, pending, done, nextTime === 0)) return
    if (start < 0) start = t
    else if (nextTime < t) gaps += t - nextTime
    nextTime = Math.max(nextTime, t) + pending
    pending = 0
  })
  return { start, gaps }
}

describe('shouldStartPlayback', () => {
  it('waits for a start buffer, then appends while playing', () => {
    expect(shouldStartPlayback(0, START_BUFFER_S - 0.01, false, true)).toBe(false)
    expect(shouldStartPlayback(0, START_BUFFER_S, false, true)).toBe(true)
    expect(shouldStartPlayback(LOW_WATER_S + 0.1, 0.1, false, false)).toBe(true)
    expect(shouldStartPlayback(0, 0.3, false, false)).toBe(false)
    expect(shouldStartPlayback(0, 0.1, true, true)).toBe(true)
  })

  it('plays a real MiniMax stream without gaps', () => {
    // Measured 2026-11-09: speech-2.8-turbo, 60 chars, (arrival s, audio s).
    const real: [number, number][] = [
      [3.27, 0.28], [3.41, 0.64], [4.91, 0.85], [5.81, 1.53], [6.98, 1.53],
      [7.73, 1.53], [9.31, 1.53], [10.48, 1.53], [10.73, 1.42]
    ]
    const r = simulate(real)
    expect(r.gaps).toBe(0)
    expect(r.start).toBeCloseTo(4.91)
  })
})
