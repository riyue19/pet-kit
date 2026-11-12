import { describe, expect, it } from 'vitest'
import { makeTargetId, validateSkills, type SkillsConfig, type SkillTarget } from '../../frontend/src/renderer/console/skills'

describe('makeTargetId', () => {
  it('uses the lowercase file stem, handling both separators', () => {
    expect(makeTargetId('C:\\Program Files\\Foo\\Notepad.exe', [])).toBe('notepad')
    expect(makeTargetId('/usr/bin/Calc.exe', [])).toBe('calc')
  })
  it('replaces invalid chars and collapses underscores', () => {
    expect(makeTargetId('C:\\x\\My--Cool  App!.exe', [])).toBe('my_cool_app')
  })
  it('prefixes app_ when empty or not starting with a letter', () => {
    expect(makeTargetId('C:\\x\\7zip.exe', [])).toBe('app_7zip')
    expect(makeTargetId('C:\\x\\微信.exe', [])).toBe('app')
  })
  it('truncates to 32 chars', () => {
    expect(makeTargetId('a'.repeat(50) + '.exe', [])).toHaveLength(32)
  })
  it('deduplicates with numeric suffix within 32 chars', () => {
    expect(makeTargetId('notepad.exe', ['notepad'])).toBe('notepad_2')
    expect(makeTargetId('notepad.exe', ['notepad', 'notepad_2'])).toBe('notepad_3')
    const long = 'a'.repeat(32)
    const id = makeTargetId(long + '.exe', [long])
    expect(id).toHaveLength(32)
    expect(id.endsWith('_2')).toBe(true)
  })
})

const target = (over: Partial<SkillTarget> = {}): SkillTarget => ({
  label: 'X',
  exe: 'C:\\x.exe',
  args: [],
  url: null,
  search_url: null,
  credential: null,
  ...over
})
const cfg = (targets: Record<string, SkillTarget>, name = 'open_app'): SkillsConfig => ({
  enabled: true,
  confirm_timeout_seconds: 30,
  skills: { [name]: { enabled: true, confirm: false, targets } }
})

describe('validateSkills', () => {
  it('accepts a valid config', () => {
    expect(validateSkills(cfg({ notepad: target() }))).toBeNull()
  })
  it('rejects a bad id', () => {
    expect(validateSkills(cfg({ Bad: target() }))).toContain('id')
  })
  it('rejects an empty label', () => {
    expect(validateSkills(cfg({ a: target({ label: ' ' }) }))).toContain('名称')
  })
  it('requires exactly one of exe/url', () => {
    expect(validateSkills(cfg({ a: target({ exe: '' }) }))).toContain('必须填写其中一个')
    expect(validateSkills(cfg({ a: target({ exe: 'a.exe', url: 'x://y' }) }))).toContain('必须填写其中一个')
    expect(validateSkills(cfg({ a: target({ exe: null, url: 'x://y' }) }))).toBeNull()
  })
  it('requires {query} in search_url', () => {
    expect(validateSkills(cfg({ a: target({ search_url: 'x://s' }) }, 'play_music'))).toContain('{query}')
    expect(validateSkills(cfg({ a: target({ search_url: 'x://s?{query}' }) }, 'play_music'))).toBeNull()
  })
  it('requires {secret} in args when credential is set', () => {
    expect(validateSkills(cfg({ a: target({ credential: 'me' }) }, 'open_game'))).toContain('{secret}')
    expect(validateSkills(cfg({ a: target({ credential: 'me', args: ['--pw={secret}'] }) }, 'open_game'))).toBeNull()
  })
  it('names the skill in the error', () => {
    expect(validateSkills(cfg({ a: target({ label: '' }) }, 'open_game'))).toContain('开游戏')
  })
})
