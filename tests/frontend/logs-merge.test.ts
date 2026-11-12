import { describe, expect, it } from 'vitest'
import type { LogEntry } from '../../frontend/src/shared/backend'
import { countLevels, formatTime, levelTagType, mergeRows, toRows } from '../../frontend/src/renderer/console/logs-util'

const e = (seq: number, ts: number, level: LogEntry['level'] = 'INFO'): LogEntry => ({
  seq,
  ts,
  level,
  logger: 'x',
  file: 'a.py',
  line: 1,
  func: 'f',
  msg: 'm',
  event_id: null,
  exc: null
})

describe('mergeRows', () => {
  it('merges two sources by ts', () => {
    const a = toRows([e(1, 10), e(2, 30)], 'backend')
    const b = toRows([e(1, 20)], 'frontend')
    const out = mergeRows(a, b, 100)
    expect(out.map((r) => r.key)).toEqual(['backend:1', 'frontend:1', 'backend:2'])
  })

  it('dedupes by source and seq', () => {
    const a = toRows([e(1, 10)], 'backend')
    expect(mergeRows(a, toRows([e(1, 10)], 'backend'), 100)).toHaveLength(1)
    expect(mergeRows(a, toRows([e(1, 10)], 'frontend'), 100)).toHaveLength(2)
  })

  it('drops the oldest beyond max', () => {
    const out = mergeRows([], toRows([e(1, 1), e(2, 2), e(3, 3)], 'backend'), 2)
    expect(out.map((r) => r.seq)).toEqual([2, 3])
  })
})

describe('helpers', () => {
  it('counts levels', () => {
    expect(countLevels([e(1, 1, 'ERROR'), e(2, 1, 'CRITICAL'), e(3, 1, 'WARNING'), e(4, 1)])).toEqual({ error: 2, warning: 1 })
  })
  it('formats time with milliseconds', () => {
    expect(formatTime(1767225600.007)).toMatch(/^\d\d:\d\d:\d\d\.00[67]$/)
  })
  it('maps tag types', () => {
    expect(levelTagType('CRITICAL')).toBe('error')
    expect(levelTagType('DEBUG')).toBe('default')
  })
})
