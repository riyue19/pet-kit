import { describe, expect, it } from 'vitest'
import {
  clampToWorkArea,
  defaultWindowPosition,
  dragPosition,
  clampDragToWorkArea,
  placeModel,
  requiredWindowSize,
  fitModel,
  screenToWindow
} from '../../frontend/src/shared/geometry'

describe('screenToWindow', () => {
  it('subtracts window origin', () => {
    expect(screenToWindow({ x: 150, y: 260 }, { x: 100, y: 200, width: 400, height: 600 })).toEqual({
      x: 50,
      y: 60
    })
  })

  it('can return values outside the window', () => {
    expect(screenToWindow({ x: 10, y: 1000 }, { x: 100, y: 200, width: 400, height: 600 })).toEqual({
      x: -90,
      y: 800
    })
  })

  it('works with negative window origin (secondary monitor on the left)', () => {
    expect(screenToWindow({ x: -1800, y: 50 }, { x: -1920, y: 0, width: 400, height: 600 })).toEqual({
      x: 120,
      y: 50
    })
  })
})

describe('dragPosition', () => {
  it('moves by cursor delta', () => {
    expect(dragPosition({ x: 100, y: 100 }, { x: 10, y: 10 }, { x: 30, y: 5 })).toEqual({
      x: 120,
      y: 95
    })
  })

  it('rounds fractional results', () => {
    expect(dragPosition({ x: 100, y: 100 }, { x: 0, y: 0 }, { x: 0.4, y: 0.6 })).toEqual({
      x: 100,
      y: 101
    })
  })
})

describe('clampToWorkArea', () => {
  const area = { x: 0, y: 0, width: 1920, height: 1040 }

  it('leaves visible windows untouched', () => {
    expect(clampToWorkArea({ x: 100, y: 100, width: 400, height: 600 }, area)).toEqual({
      x: 100,
      y: 100
    })
  })

  it('clamps overflow on right/bottom', () => {
    expect(clampToWorkArea({ x: 1800, y: 900, width: 400, height: 600 }, area)).toEqual({
      x: 1520,
      y: 440
    })
  })

  it('clamps overflow on left/top', () => {
    expect(clampToWorkArea({ x: -50, y: -20, width: 400, height: 600 }, area)).toEqual({
      x: 0,
      y: 0
    })
  })

  it('handles negative-origin work area (monitor left of primary)', () => {
    const left = { x: -1920, y: 0, width: 1920, height: 1080 }
    expect(clampToWorkArea({ x: -2500, y: 10, width: 400, height: 600 }, left)).toEqual({
      x: -1920,
      y: 10
    })
    expect(clampToWorkArea({ x: -100, y: 10, width: 400, height: 600 }, left)).toEqual({
      x: -400,
      y: 10
    })
  })

  it('aligns to top-left when window is larger than work area', () => {
    expect(clampToWorkArea({ x: 500, y: 500, width: 3000, height: 2000 }, area)).toEqual({
      x: 0,
      y: 0
    })
    const left = { x: -1920, y: 30, width: 800, height: 600 }
    expect(clampToWorkArea({ x: 0, y: 0, width: 1000, height: 700 }, left)).toEqual({
      x: -1920,
      y: 30
    })
  })

  it('rounds results', () => {
    expect(clampToWorkArea({ x: 10.6, y: 20.4, width: 400, height: 600 }, area)).toEqual({
      x: 11,
      y: 20
    })
  })
})

describe('defaultWindowPosition', () => {
  it('places at bottom-right of work area', () => {
    expect(
      defaultWindowPosition({ width: 400, height: 600 }, { x: 0, y: 0, width: 1920, height: 1040 })
    ).toEqual({ x: 1520, y: 440 })
  })

  it('applies margin', () => {
    expect(
      defaultWindowPosition({ width: 400, height: 600 }, { x: 0, y: 0, width: 1920, height: 1040 }, 20)
    ).toEqual({ x: 1500, y: 420 })
  })

  it('works with negative-origin work area', () => {
    expect(
      defaultWindowPosition({ width: 400, height: 600 }, { x: -1920, y: 0, width: 1920, height: 1080 })
    ).toEqual({ x: -400, y: 480 })
  })

  it('clamps when window is larger than work area', () => {
    expect(
      defaultWindowPosition({ width: 3000, height: 2000 }, { x: -1920, y: 10, width: 1920, height: 1080 })
    ).toEqual({ x: -1920, y: 10 })
  })
})

describe('fitModel', () => {
  it('width-limited: model wider than canvas ratio', () => {
    // model 2000x1000 into 400x600: scale = min(0.2, 0.6) = 0.2
    const r = fitModel({ width: 2000, height: 1000 }, { width: 400, height: 600 })
    expect(r.scale).toBeCloseTo(0.2)
    expect(r.x).toBeCloseTo(0)
    expect(r.y).toBeCloseTo(600 - 200)
  })

  it('height-limited: model taller than canvas ratio', () => {
    // model 1000x3000 into 400x600: scale = min(0.4, 0.2) = 0.2
    const r = fitModel({ width: 1000, height: 3000 }, { width: 400, height: 600 })
    expect(r.scale).toBeCloseTo(0.2)
    expect(r.x).toBeCloseTo((400 - 200) / 2)
    expect(r.y).toBeCloseTo(0)
  })

  it('respects padding', () => {
    // canvas 400x600, p=20 -> available 360x560; model 360x560 -> scale 1
    const r = fitModel({ width: 360, height: 560 }, { width: 400, height: 600 }, 20)
    expect(r.scale).toBeCloseTo(1)
    expect(r.x).toBeCloseTo(20)
    expect(r.y).toBeCloseTo(20)
  })

  it('bottom-aligns with padding', () => {
    // model 100x100 into 400x600, p=10: scale=min(3.8,5.8)=3.8, h=380, y=600-10-380=210
    const r = fitModel({ width: 100, height: 100 }, { width: 400, height: 600 }, 10)
    expect(r.scale).toBeCloseTo(3.8)
    expect(r.x).toBeCloseTo(10)
    expect(r.y).toBeCloseTo(210)
  })

  it('throws RangeError for non-positive model size', () => {
    expect(() => fitModel({ width: 0, height: 100 }, { width: 400, height: 600 })).toThrow(RangeError)
    expect(() => fitModel({ width: 100, height: 0 }, { width: 400, height: 600 })).toThrow(RangeError)
    expect(() => fitModel({ width: -5, height: 100 }, { width: 400, height: 600 })).toThrow(RangeError)
  })
})

describe('placeModel', () => {
  const model = { width: 100, height: 200 }
  const area = { x: 0, y: 0, width: 200, height: 200 }

  it('full framing fits the whole model bottom-aligned without clip', () => {
    const p = placeModel(model, area, { scale: 'auto', framing: 'full', halfRatio: 0.5 }, 10)
    expect(p.scale).toBeCloseTo(0.9)
    expect(p.x).toBeCloseTo(55)
    expect(p.y).toBeCloseTo(10)
    expect(p.clip).toBeNull()
  })

  it('half framing enlarges the visible top part and puts the cut on the area bottom', () => {
    const p = placeModel(model, area, { scale: 'auto', framing: 'half', halfRatio: 0.5 }, 10)
    // visible height 100: min((200-20)/100, (200-10)/100) = 1.8
    expect(p.scale).toBeCloseTo(1.8)
    expect(p.x).toBeCloseTo(10)
    expect(p.y + 100 * p.scale).toBeCloseTo(200)
    expect(p.clip).toEqual(area)
  })

  it('numeric scale overrides auto fit and respects area offset', () => {
    const offset = { x: 50, y: 20, width: 200, height: 100 }
    const p = placeModel(model, offset, { scale: 0.5, framing: 'full', halfRatio: 0.5 })
    expect(p.scale).toBe(0.5)
    expect(p.x).toBeCloseTo(50 + (200 - 50) / 2)
    expect(p.y).toBeCloseTo(20 + 100 - 100)
  })

  it('rejects invalid model size', () => {
    expect(() => placeModel({ width: 0, height: 1 }, area, { scale: 'auto', framing: 'full', halfRatio: 1 })).toThrow(RangeError)
  })
})

describe('requiredWindowSize', () => {
  const model = { width: 1000, height: 2000 }
  it('is null for auto scale', () => {
    expect(requiredWindowSize(model, { scale: 'auto', framing: 'full', halfRatio: 0.5 }, 0.2, 8)).toBeNull()
  })
  it('holds a full-body model at a fixed scale, leaving the chat strip', () => {
    const s = requiredWindowSize(model, { scale: 0.5, framing: 'full', halfRatio: 0.5 }, 0.2, 8)!
    expect(s.width).toBe(516)
    expect(s.height).toBe(Math.ceil((1000 + 16) / 0.8))
    // and placeModel then fits inside the area that size leaves
    const p = placeModel(model, { x: 0, y: 0, width: s.width, height: s.height * 0.8 }, { scale: 0.5, framing: 'full', halfRatio: 0.5 }, 8)
    expect(p.x).toBeGreaterThanOrEqual(8)
    expect(p.y).toBeGreaterThanOrEqual(7.9)
  })
  it('half framing only needs the visible part', () => {
    const s = requiredWindowSize(model, { scale: 1, framing: 'half', halfRatio: 0.4 }, 0.2, 8)!
    expect(s.height).toBe(Math.ceil((800 + 8) / 0.8))
  })
})

describe('clampDragToWorkArea', () => {
  const wa = { x: 0, y: 0, width: 1920, height: 1040 }
  const size = { width: 400, height: 600 }
  it('lets the window leave the screen sideways but keeps a strip visible', () => {
    expect(clampDragToWorkArea({ x: -250, y: 100, ...size }, wa).x).toBe(-250)
    expect(clampDragToWorkArea({ x: -5000, y: 100, ...size }, wa).x).toBe(-380)
    expect(clampDragToWorkArea({ x: 1800, y: 100, ...size }, wa).x).toBe(1800)
    expect(clampDragToWorkArea({ x: 9000, y: 100, ...size }, wa).x).toBe(1900)
  })
  it('keeps the window vertically inside', () => {
    expect(clampDragToWorkArea({ x: 0, y: -50, ...size }, wa).y).toBe(0)
    expect(clampDragToWorkArea({ x: 0, y: 900, ...size }, wa).y).toBe(440)
  })
})
