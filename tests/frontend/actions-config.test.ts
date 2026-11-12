import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { mkdir, mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises'
import {
  actionsConfigPath,
  loadActionMap,
  parseActionMap,
  readModelInventory,
  saveActionMap,
  validateActionMap
} from '../../frontend/src/main/actions-config'
import { ConfigError } from '../../frontend/src/main/config'
import { DEFAULT_ACTION_MAP } from '../../frontend/src/shared/agent'

let dir: string
beforeEach(async () => {
  dir = await mkdtemp(join(tmpdir(), 'pet-actions-'))
})
afterEach(async () => {
  await rm(dir, { recursive: true, force: true })
})

const good = {
  expressions: { happy: 'F01', sad: 'F02' },
  motions: { wave: { group: 'TapBody', index: 1 } },
  holdSeconds: 8
}

function thrown(fn: () => unknown): Error {
  try {
    fn()
  } catch (e) {
    return e as Error
  }
  throw new Error('expected throw')
}

describe('actionsConfigPath', () => {
  it('derives from model directory name', () => {
    expect(actionsConfigPath('/cfg', 'live2d/models/10991/10991.model3.json')).toBe(
      join('/cfg', 'models', '10991.yaml')
    )
  })
  it('accepts non-ASCII directory names (the importer keeps letters of any language)', () => {
    expect(actionsConfigPath('/cfg', 'live2d/models/测试_模型_/示例模型.model3.json')).toBe(
      join('/cfg', 'models', '测试_模型_.yaml')
    )
  })
  it('still rejects dot-dirs and characters that are unsafe in file names', () => {
    for (const dir of ['..', '.', 'a:b', 'a<b', 'a*b', '测试【皮套】']) {
      expect(actionsConfigPath('/cfg', `live2d/models/${dir}/x.model3.json`)).toBeNull()
    }
  })
  it('returns null for empty path', () => {
    expect(actionsConfigPath('/cfg', '')).toBeNull()
  })
  it('returns null for invalid directory names', () => {
    expect(actionsConfigPath('/cfg', 'live2d/models/bad$name/x.model3.json')).toBeNull()
    expect(actionsConfigPath('/cfg', 'live2d/models/' + 'a'.repeat(65) + '/x.model3.json')).toBeNull()
    expect(actionsConfigPath('/cfg', 'live2d/models/../x.model3.json')).toBeNull()
    expect(actionsConfigPath('/cfg', 'x.model3.json')).toBeNull()
  })
})

describe('parseActionMap', () => {
  it('null/undefined/empty yield deep-copied defaults', () => {
    const a = parseActionMap(null)
    expect(a).toEqual(DEFAULT_ACTION_MAP)
    a.expressions.x = 'y'
    expect(DEFAULT_ACTION_MAP.expressions).toEqual({})
    expect(parseActionMap(undefined)).toEqual(DEFAULT_ACTION_MAP)
    expect(parseActionMap({})).toEqual(DEFAULT_ACTION_MAP)
  })
  it('parses a valid map and fills missing fields', () => {
    expect(
      parseActionMap({ expressions: { happy: 'F01' }, hold_seconds: 3 })
    ).toEqual({ expressions: { happy: 'F01' }, motions: {}, holdSeconds: 3 })
  })
  it('rejects bad label with field path in message', () => {
    const e = thrown(() => parseActionMap({ expressions: { Happy: 'F01' } }))
    expect(e).toBeInstanceOf(ConfigError)
    expect(e.message).toContain('expressions.Happy: 标签只能是小写字母开头的 a-z0-9_，最长 32')
  })
  it('rejects unknown top-level key', () => {
    expect(thrown(() => parseActionMap({ foo: 1 }))).toBeInstanceOf(ConfigError)
  })
  it('rejects non-mapping root and non-mapping sections', () => {
    expect(thrown(() => parseActionMap([]))).toBeInstanceOf(ConfigError)
    expect(thrown(() => parseActionMap({ expressions: [] })).message).toContain('expressions')
    expect(thrown(() => parseActionMap({ motions: 'x' })).message).toContain('motions')
  })
  it('rejects hold_seconds out of range', () => {
    expect(thrown(() => parseActionMap({ hold_seconds: 0 })).message).toContain('hold_seconds')
    expect(thrown(() => parseActionMap({ hold_seconds: 61 })).message).toContain('hold_seconds')
    expect(thrown(() => parseActionMap({ hold_seconds: '5' })).message).toContain('hold_seconds')
  })
})

describe('validateActionMap', () => {
  it('returns a new equal object', () => {
    const v = validateActionMap(good)
    expect(v).toEqual(good)
    expect(v).not.toBe(good)
    expect(v.expressions).not.toBe(good.expressions)
  })
  it('requires all three fields', () => {
    for (const k of ['expressions', 'motions', 'holdSeconds']) {
      const bad: Record<string, unknown> = { ...good }
      delete bad[k]
      expect(thrown(() => validateActionMap(bad)).message).toContain(k)
    }
  })
  it('rejects non-object and extra keys', () => {
    expect(() => validateActionMap(null)).toThrow()
    expect(() => validateActionMap('x')).toThrow()
    expect(() => validateActionMap({ ...good, extra: 1 })).toThrow(/extra/)
  })
  it('rejects bad labels', () => {
    expect(() => validateActionMap({ ...good, expressions: { '1a': 'x' } })).toThrow(/expressions\.1a/)
    expect(() => validateActionMap({ ...good, motions: { 'A': { group: 'g', index: 0 } } })).toThrow(
      /motions\.A/
    )
    expect(() =>
      validateActionMap({ ...good, expressions: { ['a'.repeat(33)]: 'x' } })
    ).toThrow(/标签/)
  })
  it('rejects more than 64 labels', () => {
    const many: Record<string, string> = {}
    for (let i = 0; i < 65; i++) many[`l${i}`] = 'x'
    expect(() => validateActionMap({ ...good, expressions: many })).toThrow(/64/)
    const manyM: Record<string, unknown> = {}
    for (let i = 0; i < 65; i++) manyM[`l${i}`] = { group: 'g', index: 0 }
    expect(() => validateActionMap({ ...good, motions: manyM })).toThrow(/64/)
  })
  it('rejects bad expression values', () => {
    expect(() => validateActionMap({ ...good, expressions: { happy: '' } })).toThrow(/expressions\.happy/)
    expect(() => validateActionMap({ ...good, expressions: { happy: 1 } })).toThrow(/expressions\.happy/)
    expect(() =>
      validateActionMap({ ...good, expressions: { happy: 'x'.repeat(65) } })
    ).toThrow(/expressions\.happy/)
  })
  it('rejects bad motion values', () => {
    const m = (v: unknown): unknown => ({ ...good, motions: { wave: v } })
    expect(() => validateActionMap(m('x'))).toThrow(/motions\.wave/)
    expect(() => validateActionMap(m({ group: '', index: 0 }))).toThrow(/group/)
    expect(() => validateActionMap(m({ group: 'g', index: -1 }))).toThrow(/index/)
    expect(() => validateActionMap(m({ group: 'g', index: 1.5 }))).toThrow(/index/)
    expect(() => validateActionMap(m({ group: 'g' }))).toThrow(/index/)
    expect(() => validateActionMap(m({ group: 'g', index: 0, extra: 1 }))).toThrow(/extra/)
  })
  it('keeps an optional paired expression on motions', () => {
    const m = (v: unknown): unknown => ({ ...good, motions: { coin: v } })
    expect(validateActionMap(m({ group: 'g', index: 0, expression: '抛硬币' })).motions.coin).toEqual({
      group: 'g',
      index: 0,
      expression: '抛硬币'
    })
    expect(validateActionMap(m({ group: 'g', index: 0, expression: null })).motions.coin).toEqual({ group: 'g', index: 0 })
    expect(() => validateActionMap(m({ group: 'g', index: 0, expression: '' }))).toThrow(/expression/)
    expect(() => validateActionMap(m({ group: 'g', index: 0, expression: 3 }))).toThrow(/expression/)
    expect(parseActionMap({ motions: { coin: { group: 'g', index: 0, expression: 'e' } } }).motions.coin.expression).toBe('e')
  })
  it('keeps loop only when true', () => {
    const m = (v: unknown): unknown => ({ ...good, motions: { nap: v } })
    expect(validateActionMap(m({ group: 'g', index: 0, loop: true })).motions.nap).toEqual({ group: 'g', index: 0, loop: true })
    expect(validateActionMap(m({ group: 'g', index: 0, loop: false })).motions.nap).toEqual({ group: 'g', index: 0 })
    expect(() => validateActionMap(m({ group: 'g', index: 0, loop: 'yes' }))).toThrow(/loop/)
  })
  it('rejects holdSeconds out of range or NaN', () => {
    expect(() => validateActionMap({ ...good, holdSeconds: 0.5 })).toThrow(/holdSeconds/)
    expect(() => validateActionMap({ ...good, holdSeconds: 100 })).toThrow(/holdSeconds/)
    expect(() => validateActionMap({ ...good, holdSeconds: NaN })).toThrow(/holdSeconds/)
  })
})

describe('loadActionMap / saveActionMap', () => {
  it('null or missing file yields defaults', async () => {
    expect(await loadActionMap(null)).toEqual(DEFAULT_ACTION_MAP)
    expect(await loadActionMap(join(dir, 'nope.yaml'))).toEqual(DEFAULT_ACTION_MAP)
  })
  it('round-trips with comment header, creating parent dirs, leaving no tmp', async () => {
    const file = join(dir, 'models', 'sub', 'm.yaml')
    const saved = await saveActionMap(file, good)
    expect(saved).toEqual(good)
    const text = await readFile(file, 'utf8')
    expect(text.startsWith('# 表情/动作映射')).toBe(true)
    expect(text).toContain('hold_seconds: 8')
    expect(await loadActionMap(file)).toEqual(good)
    expect(await readdir(join(dir, 'models', 'sub'))).toEqual(['m.yaml'])
  })
  it('round-trips empty maps', async () => {
    const file = join(dir, 'e.yaml')
    const empty = { expressions: {}, motions: {}, holdSeconds: 6 }
    await saveActionMap(file, empty)
    expect(await loadActionMap(file)).toEqual(empty)
  })
  it('save rejects invalid maps without writing', async () => {
    const file = join(dir, 'bad.yaml')
    await expect(saveActionMap(file, { ...good, holdSeconds: 0 })).rejects.toThrow(/holdSeconds/)
    expect(await readdir(dir)).toEqual([])
  })
  it('load reports YAML syntax errors and validation errors with file path', async () => {
    const f1 = join(dir, 'syntax.yaml')
    await writeFile(f1, 'expressions: [unclosed\n', 'utf8')
    const e1 = await loadActionMap(f1).catch((e: unknown) => e as Error)
    expect(e1).toBeInstanceOf(ConfigError)
    expect((e1 as Error).message.startsWith(f1)).toBe(true)

    const f2 = join(dir, 'invalid.yaml')
    await writeFile(f2, 'expressions:\n  Bad: x\n', 'utf8')
    const e2 = await loadActionMap(f2).catch((e: unknown) => e as Error)
    expect(e2).toBeInstanceOf(ConfigError)
    expect((e2 as Error).message.startsWith(f2)).toBe(true)
    expect((e2 as Error).message).toContain('expressions.Bad')
  })
  it('empty file yields defaults', async () => {
    const f = join(dir, 'empty.yaml')
    await writeFile(f, '# only comments\n', 'utf8')
    expect(await loadActionMap(f)).toEqual(DEFAULT_ACTION_MAP)
  })
})

describe('readModelInventory', () => {
  const rel = 'live2d/models/m/m.model3.json'
  async function put(content: string): Promise<void> {
    await mkdir(join(dir, 'live2d/models/m'), { recursive: true })
    await writeFile(join(dir, rel), content, 'utf8')
  }
  const empty = { expressions: [], motions: [] }

  it('reads expressions and motion groups', async () => {
    await put(
      JSON.stringify({
        FileReferences: {
          Expressions: [{ Name: 'F01', File: 'a.exp3.json' }, { File: 'noname' }, { Name: 5 }, { Name: 'F02' }],
          Motions: {
            Idle: [{ File: 'a' }, { File: 'b' }],
            TapBody: [{ File: 'c' }],
            Empty: [],
            Bad: 'x'
          }
        }
      })
    )
    expect(await readModelInventory(dir, rel)).toEqual({
      expressions: ['F01', 'F02'],
      motions: [
        { group: 'Idle', count: 2 },
        { group: 'TapBody', count: 1 }
      ]
    })
  })
  it('model without expressions/motions yields empty lists', async () => {
    await put(JSON.stringify({ FileReferences: { Moc: 'a.moc3' } }))
    expect(await readModelInventory(dir, rel)).toEqual(empty)
  })
  it('missing file, bad JSON, wrong structure, empty path yield empty', async () => {
    expect(await readModelInventory(dir, rel)).toEqual(empty)
    await put('{ not json')
    expect(await readModelInventory(dir, rel)).toEqual(empty)
    await put('[1,2]')
    expect(await readModelInventory(dir, rel)).toEqual(empty)
    await put(JSON.stringify({ FileReferences: 3 }))
    expect(await readModelInventory(dir, rel)).toEqual(empty)
    expect(await readModelInventory(dir, '')).toEqual(empty)
  })
})
