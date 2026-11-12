import { describe, expect, it } from 'vitest'
import { MouthFollower, PcmDecoder } from '../../frontend/src/renderer/pcm'

function le16(...vals: number[]): Uint8Array {
  const b = new Uint8Array(vals.length * 2)
  const v = new DataView(b.buffer)
  vals.forEach((x, i) => v.setInt16(i * 2, x, true))
  return b
}

describe('PcmDecoder', () => {
  it('decodes 16-bit little-endian samples', () => {
    const out = new PcmDecoder().decode(le16(0, 16384, -32768))
    expect(Array.from(out)).toEqual([0, 0.5, -1])
  })

  it('carries a sample split across chunks', () => {
    const bytes = le16(1000, -2000, 3000)
    const d = new PcmDecoder()
    const a = d.decode(bytes.slice(0, 3))
    const b = d.decode(bytes.slice(3))
    expect(a.length).toBe(1)
    expect(Array.from([...a, ...b]).map((x) => Math.round(x * 32768))).toEqual([1000, -2000, 3000])
  })

  it('reset drops a pending half sample', () => {
    const d = new PcmDecoder()
    d.decode(new Uint8Array([1]))
    d.reset()
    expect(Array.from(d.decode(le16(16384)))).toEqual([0.5])
  })

  it('handles views with a non-zero, odd byte offset', () => {
    const backing = new Uint8Array(7)
    backing.set(le16(16384, -16384), 1)
    expect(Array.from(new PcmDecoder().decode(backing.subarray(1, 5)))).toEqual([0.5, -0.5])
  })
})

describe('MouthFollower', () => {
  it('stays closed in silence and opens with speech', () => {
    const m = new MouthFollower()
    expect(m.next(0)).toBe(0)
    let v = 0
    for (let i = 0; i < 10; i++) v = m.next(0.2)
    expect(v).toBeGreaterThan(0.6)
    for (let i = 0; i < 30; i++) v = m.next(0)
    expect(v).toBe(0)
  })

  it('never exceeds 1', () => {
    const m = new MouthFollower()
    for (let i = 0; i < 20; i++) expect(m.next(5)).toBeLessThanOrEqual(1)
  })
})
