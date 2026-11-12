import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { HotkeyManager, loadHotkeys, readHotkeysFile, saveHotkeys, type ShortcutApi } from '../../frontend/src/main/hotkeys'
import {
  acceleratorFromKeyEvent,
  normalizeAccelerator,
  parseHotkeysLenient,
  validateHotkeys,
  type Hotkey
} from '../../frontend/src/shared/hotkeys'

const base: Hotkey = {
  id: 'greet',
  name: '打招呼',
  keys: 'Ctrl+Alt+W',
  enabled: true,
  action: 'reaction',
  builtin: null,
  lines: ['嗨'],
  expressions: [],
  motions: [],
  prompt: '',
  cooldown_seconds: 2
}

describe('normalizeAccelerator', () => {
  it.each([
    ['ctrl+alt+h', 'Ctrl+Alt+H'],
    ['Alt + Ctrl + h', 'Ctrl+Alt+H'],
    ['CommandOrControl+Shift+1', 'Ctrl+Shift+1'],
    ['Win+Space', 'Super+Space'],
    ['F9', 'F9'],
    ['Shift+F12', 'Shift+F12'],
    ['Ctrl+Alt+Return', 'Ctrl+Alt+Enter'],
    ['Ctrl+/', 'Ctrl+/']
  ])('%s -> %s', (raw, want) => {
    expect(normalizeAccelerator(raw)).toEqual({ ok: true, value: want })
  })

  it.each(['H', 'Shift+H', 'Ctrl+Alt', 'Ctrl+A+B', 'Ctrl+Foo', '', 'Ctrl++'])('rejects %s', (raw) => {
    expect(normalizeAccelerator(raw).ok).toBe(false)
  })
})

describe('acceleratorFromKeyEvent', () => {
  const ev = (code: string, key: string, mods: Partial<Record<'ctrlKey' | 'altKey' | 'shiftKey' | 'metaKey', boolean>> = {}) => ({
    code,
    key,
    ctrlKey: false,
    altKey: false,
    shiftKey: false,
    metaKey: false,
    ...mods
  })
  it('maps physical keys regardless of layout/IME', () => {
    expect(acceleratorFromKeyEvent(ev('KeyH', 'ｈ', { ctrlKey: true, altKey: true }))).toBe('Ctrl+Alt+H')
    expect(acceleratorFromKeyEvent(ev('Digit1', '!', { ctrlKey: true, shiftKey: true }))).toBe('Ctrl+Shift+1')
    expect(acceleratorFromKeyEvent(ev('ArrowUp', 'ArrowUp', { altKey: true }))).toBe('Alt+Up')
    expect(acceleratorFromKeyEvent(ev('F5', 'F5'))).toBe('F5')
  })
  it('waits while only modifiers are held', () => {
    expect(acceleratorFromKeyEvent(ev('ControlLeft', 'Control', { ctrlKey: true }))).toBeNull()
  })
})

describe('validateHotkeys', () => {
  it('cleans and normalizes', () => {
    const [h] = validateHotkeys([{ ...base, keys: 'alt+ctrl+w', lines: [' 嗨 ', ''], extra: 1 }])
    expect(h.keys).toBe('Ctrl+Alt+W')
    expect(h.lines).toEqual(['嗨'])
    expect(h).not.toHaveProperty('extra')
  })
  it.each([
    [[{ ...base, id: 'Bad' }], 'id'],
    [[base, { ...base }], 'id 重复'],
    [[base, { ...base, id: 'other' }], '同一个组合键'],
    [[{ ...base, action: 'llm', prompt: '' }], '提示'],
    [[{ ...base, action: 'builtin', builtin: null }], '内置动作'],
    [[{ ...base, lines: [] }], '至少'],
    [[{ ...base, keys: 'H' }], 'Ctrl']
  ])('rejects %#', (list, msg) => {
    expect(() => validateHotkeys(list)).toThrow(msg)
  })
  it('allows the same combo when one is disabled', () => {
    expect(validateHotkeys([base, { ...base, id: 'other', enabled: false }])).toHaveLength(2)
  })
})

describe('HotkeyManager', () => {
  function fakeApi(taken: string[] = []) {
    const registered = new Map<string, () => void>()
    const api: ShortcutApi = {
      register: (acc, cb) => {
        if (taken.includes(acc)) return false
        registered.set(acc, cb)
        return true
      },
      unregister: (acc) => void registered.delete(acc)
    }
    return { api, registered }
  }

  it('registers enabled combos, reports conflicts, fires by id', () => {
    const { api, registered } = fakeApi(['Ctrl+Alt+H'])
    const fired: string[] = []
    const m = new HotkeyManager(api, (h) => fired.push(h.id))
    const status = m.apply([
      base,
      { ...base, id: 'praise', keys: 'Ctrl+Alt+H', action: 'llm', prompt: 'p' },
      { ...base, id: 'off', keys: 'Ctrl+Alt+O', enabled: false }
    ])
    expect(status.map((s) => [s.id, s.ok])).toEqual([
      ['greet', true],
      ['praise', false],
      ['off', false]
    ])
    expect(status[1].error).toContain('占用')
    expect([...registered.keys()]).toEqual(['Ctrl+Alt+W'])
    registered.get('Ctrl+Alt+W')!()
    expect(fired).toEqual(['greet'])
  })

  it('suspends while recording and re-registers after', () => {
    const { api, registered } = fakeApi()
    const m = new HotkeyManager(api, () => undefined)
    m.apply([base])
    m.setSuspended(true)
    expect(registered.size).toBe(0)
    expect(m.current.status[0].ok).toBe(false)
    m.setSuspended(false)
    expect([...registered.keys()]).toEqual(['Ctrl+Alt+W'])
    m.dispose()
    expect(registered.size).toBe(0)
  })

  it('re-applying unregisters removed combos', () => {
    const { api, registered } = fakeApi()
    const m = new HotkeyManager(api, () => undefined)
    m.apply([base])
    m.apply([{ ...base, keys: 'Ctrl+Alt+Q' }])
    expect([...registered.keys()]).toEqual(['Ctrl+Alt+Q'])
  })
})

describe('hotkeys.yaml', () => {
  let dir: string
  beforeEach(async () => {
    dir = await mkdtemp(join(tmpdir(), 'pet-hotkeys-'))
  })
  afterEach(async () => {
    await rm(dir, { recursive: true, force: true })
  })

  it('round-trips and keeps the header comment', async () => {
    const file = join(dir, 'hotkeys.yaml')
    await writeFile(file, '# 我的注释\nhotkeys: []\n', 'utf8')
    await saveHotkeys(file, [base, { ...base, id: 'mic', keys: 'Ctrl+Alt+M', action: 'builtin', builtin: 'toggle_mic' }])
    const text = await readFile(file, 'utf8')
    expect(text).toContain('# 我的注释')
    expect(text).not.toContain('prompt') // fields of other actions are left out
    const loaded = await loadHotkeys(file)
    expect(loaded.map((h) => [h.id, h.keys, h.action, h.builtin])).toEqual([
      ['greet', 'Ctrl+Alt+W', 'reaction', null],
      ['mic', 'Ctrl+Alt+M', 'builtin', 'toggle_mic']
    ])
  })

  it('missing file means no hotkeys; bad file is a ConfigError', async () => {
    expect(await loadHotkeys(join(dir, 'none.yaml'))).toEqual([])
    const bad = join(dir, 'bad.yaml')
    await writeFile(bad, 'hotkeys:\n  - id: X\n', 'utf8')
    await expect(loadHotkeys(bad)).rejects.toThrow('bad.yaml')
  })

  it('the shipped config/hotkeys.yaml is valid', async () => {
    const shipped = await loadHotkeys(join(__dirname, '../../config/hotkeys.yaml'))
    expect(shipped.length).toBeGreaterThan(0)
  })
})

describe('lenient file reading (matches the backend)', () => {
  it('skips only the bad entry', () => {
    const { hotkeys, skipped } = parseHotkeysLenient([
      base,
      { ...base, id: 'bad', keys: 'H' },
      { ...base, id: 'dupkeys' },
      { ...base, id: 'ok2', keys: 'Ctrl+Alt+Q', note: 'unknown field is ignored' }
    ])
    expect(hotkeys.map((h) => h.id)).toEqual(['greet', 'ok2'])
    expect(skipped).toHaveLength(2)
    expect(skipped[0]).toContain('第 2 项')
  })
  it('numbers become text; llm cooldown has a floor', () => {
    const [h] = validateHotkeys([{ ...base, lines: [123, ' '], name: 7 }])
    expect(h.lines).toEqual(['123'])
    expect(h.name).toBe('7')
    const [l] = validateHotkeys([{ ...base, action: 'llm', prompt: 'p', cooldown_seconds: 0 }])
    expect(l.cooldown_seconds).toBe(3)
  })
  it('readHotkeysFile reports skipped entries instead of failing', async () => {
    const dir = await mkdtemp(join(tmpdir(), 'pet-hk-lenient-'))
    try {
      const file = join(dir, 'hotkeys.yaml')
      await writeFile(file, 'hotkeys:\n  - {id: a, keys: Ctrl+Alt+A, action: reaction, lines: [x]}\n  - {id: b, keys: B, action: reaction, lines: [y]}\n', 'utf8')
      const r = await readHotkeysFile(file)
      expect(r.hotkeys.map((h) => h.id)).toEqual(['a'])
      expect(r.skipped).toHaveLength(1)
    } finally {
      await rm(dir, { recursive: true, force: true })
    }
  })
})

describe('HotkeyManager after dispose', () => {
  it('never registers again', () => {
    const registered: string[] = []
    const m = new HotkeyManager({ register: (a) => (registered.push(a), true), unregister: () => undefined }, () => undefined)
    m.apply([base])
    m.dispose()
    m.setSuspended(true)
    m.setSuspended(false)
    expect(registered).toEqual(['Ctrl+Alt+W'])
  })
})
