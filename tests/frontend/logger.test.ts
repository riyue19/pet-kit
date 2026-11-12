import { mkdtemp, readdir, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { callerSite, FrontendLog, redact } from '../../frontend/src/main/logger'

describe('redact', () => {
  it('masks api keys and bearer tokens', () => {
    expect(redact('key sk-abcdefghijklmnop end')).toBe('key *** end')
    expect(redact('Authorization: Bearer abcdefghijkl')).toBe('Authorization: Bearer ***')
    expect(redact('nothing here')).toBe('nothing here')
  })
})

describe('callerSite', () => {
  it('parses V8 stack frames', () => {
    const stack = [
      'Error',
      '    at wrap (E:\\pet-kit\\frontend\\out\\main\\logger.js:10:5)',
      '    at PetLink.connect (E:\\pet-kit\\frontend\\src\\main\\pet-link.ts:165:13)',
      '    at E:\\pet-kit\\frontend\\src\\main\\index.ts:40:1'
    ].join('\n')
    expect(callerSite(stack)).toEqual({ func: 'wrap', file: 'out/main/logger.js', line: 10 })
    expect(callerSite(stack, 1)).toEqual({ func: 'PetLink.connect', file: 'src/main/pet-link.ts', line: 165 })
    expect(callerSite(stack, 2)).toEqual({ func: '', file: 'src/main/index.ts', line: 40 })
    expect(callerSite(undefined)).toEqual({})
  })
})

describe('FrontendLog', () => {
  let dir: string
  beforeEach(async () => {
    dir = await mkdtemp(join(tmpdir(), 'pet-log-'))
  })
  afterEach(async () => {
    await rm(dir, { recursive: true, force: true })
  })

  it('keeps entries queryable like the backend', () => {
    const log = new FrontendLog(dir, () => new Date(2026, 10, 9, 12), false)
    log.write('INFO', 'main', 'started')
    log.write('WARNING', 'pet', 'mic failed', { file: 'src/renderer/main.ts', line: 300 })
    log.write('ERROR', 'console', 'boom', { exc: 'Error: boom\n    at x', event_id: 'evt_1' })
    expect(log.query().map((e) => e.msg)).toEqual(['started', 'mic failed', 'boom'])
    expect(log.query({ level: 'WARNING' }).map((e) => e.logger)).toEqual(['pet', 'console'])
    expect(log.query({ logger: 'pet' })[0]).toMatchObject({ file: 'src/renderer/main.ts', line: 300 })
    expect(log.query({ q: 'AT X' }).map((e) => e.msg)).toEqual(['boom'])
    expect(log.query({ event_id: 'evt_1' })).toHaveLength(1)
    expect(log.query({ after: 2 }).map((e) => e.seq)).toEqual([3])
    expect(log.query({ limit: 1 }).map((e) => e.msg)).toEqual(['boom'])
    expect(log.lastSeq).toBe(3)
  })

  it('redacts before storing', () => {
    const log = new FrontendLog(dir, () => new Date(), false)
    const e = log.write('ERROR', 'main', 'bad key sk-abcdefghijklmnop', { exc: 'Bearer abcdefghijkl' })
    expect(e.msg).not.toContain('sk-abc')
    expect(e.exc).not.toContain('abcdefghijkl')
  })

  it('appends JSON lines to a per-day file and prunes old files', async () => {
    const now = new Date(2026, 10, 9, 12)
    await writeFile(join(dir, 'frontend-2026-10-01.jsonl'), '{}\n')
    await writeFile(join(dir, 'frontend-2026-11-01.jsonl'), '{}\n')
    await writeFile(join(dir, 'backend.jsonl'), '{}\n')
    const log = new FrontendLog(dir, () => now)
    log.write('INFO', 'main', '你好')
    log.write('ERROR', 'pet', 'oops')
    await log.flush()
    const lines = (await readFile(join(dir, 'frontend-2026-11-09.jsonl'), 'utf8')).trim().split('\n')
    expect(lines.map((l) => JSON.parse(l).msg)).toEqual(['你好', 'oops'])
    await log.prune()
    const names = (await readdir(dir)).sort()
    expect(names).toEqual(['backend.jsonl', 'frontend-2026-11-01.jsonl', 'frontend-2026-11-09.jsonl'])
  })

  it('recreates the folder if it disappears while running', async () => {
    const now = new Date(2026, 10, 9, 12)
    const log = new FrontendLog(dir, () => now)
    log.write('INFO', 'main', 'first')
    await log.flush()
    await rm(dir, { recursive: true, force: true })
    log.write('INFO', 'main', 'lost') // fails: folder gone, resets mkdir
    await log.flush()
    log.write('INFO', 'main', 'second')
    await log.flush()
    const lines = (await readFile(join(dir, 'frontend-2026-11-09.jsonl'), 'utf8')).trim().split('\n')
    expect(lines.map((l) => JSON.parse(l).msg)).toEqual(['second'])
  })
})
