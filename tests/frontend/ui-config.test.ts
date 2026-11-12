import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { ConfigError } from '../../frontend/src/main/config'
import { DEFAULT_UI, applyCssProposal, loadUiConfig, parseUiConfig, saveUiConfig, validateUi } from '../../frontend/src/main/ui-config'

describe('parseUiConfig', () => {
  it('returns defaults for empty input', () => {
    expect(parseUiConfig('')).toEqual(DEFAULT_UI)
  })

  it('parses snake_case and lowercases accent', () => {
    expect(parseUiConfig('theme: dark\naccent: "#ABCDEF"\nfont_size: 16\n')).toEqual({
      ...DEFAULT_UI,
      theme: 'dark',
      accent: '#abcdef',
      fontSize: 16
    })
  })

  it('parses style presets and custom css', () => {
    const ui = parseUiConfig('console_style: warm\ninput_style: glass\nbubble_style: pink\npet_css: "#bubble { color: red; }"\nconsole_css: ""\n')
    expect(ui).toMatchObject({ consoleStyle: 'warm', inputStyle: 'glass', bubbleStyle: 'pink', petCss: '#bubble { color: red; }', consoleCss: '' })
  })

  it.each([
    ['console_style: neon', /console_style/],
    ['input_style: 3', /input_style/],
    ['bubble_style: x', /bubble_style/],
    ['pet_css: 5', /pet_css/],
    [`console_css: "${'a'.repeat(20001)}"`, /console_css/]
  ])('rejects %s', (text, re) => {
    expect(() => parseUiConfig(text)).toThrow(ConfigError)
    expect(() => parseUiConfig(text)).toThrow(re)
  })

  it.each([
    ['theme: blue', /theme/],
    ['accent: red', /accent/],
    ['font_size: 11', /font_size/],
    ['font_size: 14.5', /font_size/],
    ['- a', /mapping/],
    ['theme: [', /YAML/]
  ])('rejects %s', (text, re) => {
    expect(() => parseUiConfig(text)).toThrow(ConfigError)
    expect(() => parseUiConfig(text)).toThrow(re)
  })
})

describe('style schemes', () => {
  const scheme = { name: '海盐', console_style: 'warm', input_style: 'glass', bubble_style: 'pink', pet_css: '#bubble { color: red; }', console_css: '' }

  it('parses and keeps schemes in order', () => {
    const ui = parseUiConfig(`styles:
  - name: 海盐
    console_style: warm
    input_style: glass
    bubble_style: pink
    pet_css: "#bubble { color: red; }"
  - name: 空白
`)
    expect(ui.styles.map((x) => x.name)).toEqual(['海盐', '空白'])
    expect(ui.styles[0]).toMatchObject({ consoleStyle: 'warm', inputStyle: 'glass', bubbleStyle: 'pink', petCss: '#bubble { color: red; }' })
    expect(ui.styles[1]).toMatchObject({ consoleStyle: 'default', petCss: '' })
  })

  it.each([
    [{ ...scheme, name: '' }, /name/],
    [{ ...scheme, name: 'x'.repeat(31) }, /name/],
    [{ ...scheme, input_style: 'nope' }, /styles\[0\]\.input_style/],
    [{ ...scheme, pet_css: 3 }, /pet_css/]
  ])('rejects bad scheme %#', (bad, re) => {
    expect(() => validateUi({ styles: [bad] })).toThrow(re)
  })

  it('rejects duplicate names and too many schemes', () => {
    expect(() => validateUi({ styles: [scheme, scheme] })).toThrow(/duplicate/)
    expect(() => validateUi({ styles: Array.from({ length: 31 }, (_, i) => ({ name: `s${i}` })) })).toThrow(/at most/)
  })

  it('does not share the schemes list between parses', () => {
    parseUiConfig('styles:\n  - name: a\n')
    expect(parseUiConfig('').styles).toEqual([])
  })
})

describe('load/save ui config', () => {
  let dir: string
  beforeEach(async () => {
    dir = await mkdtemp(join(tmpdir(), 'pet-ui-'))
  })
  afterEach(async () => {
    await rm(dir, { recursive: true, force: true })
  })

  it('missing file yields defaults', async () => {
    expect(await loadUiConfig(join(dir, 'none.yaml'))).toEqual(DEFAULT_UI)
  })

  it('save keeps comments and round-trips', async () => {
    const file = join(dir, 'console.yaml')
    await writeFile(file, '# 外观\ntheme: auto # 主题\n', 'utf8')
    const input = { ...DEFAULT_UI, theme: 'light', accent: '#112233', fontSize: 18, bubbleStyle: 'dark', styles: [{ name: '海盐', consoleStyle: 'warm', inputStyle: 'pixel', bubbleStyle: 'pink', petCss: '#a {\n  x: 1;\n}', consoleCss: '' }], petCss: '#bubble {\n  --b-bg: red;\n}\n' }
    const ui = await saveUiConfig(file, input)
    expect(ui).toEqual(input)
    const text = await readFile(file, 'utf8')
    expect(text).toContain('# 外观')
    expect(text).toContain('# 主题')
    expect(await loadUiConfig(file)).toEqual(ui)
  })

  it('save rejects invalid input without touching the file', async () => {
    const file = join(dir, 'console.yaml')
    await writeFile(file, 'theme: auto\n', 'utf8')
    await expect(saveUiConfig(file, { theme: 'x' })).rejects.toThrow(ConfigError)
    expect(await readFile(file, 'utf8')).toBe('theme: auto\n')
  })
})

describe('applyCssProposal', () => {
  const now = new Date(2026, 10, 10, 16, 40)
  const base = { ...DEFAULT_UI, petCss: '#bubble { color: red; }', bubbleStyle: 'dark', styles: [] }

  it('appends to the current CSS and backs up the look from before', () => {
    const r = applyCssProposal(base, '#chat-input { --i-radius: 4px; }', false, now)
    if (!r.ok) throw new Error(r.error)
    expect(r.ui.petCss).toBe('#bubble { color: red; }\n\n#chat-input { --i-radius: 4px; }')
    expect(r.ui.styles).toEqual([
      { name: 'AI 改动前 11-10 16:40', consoleStyle: 'default', inputStyle: 'default', bubbleStyle: 'dark', petCss: '#bubble { color: red; }', consoleCss: '' }
    ])
    expect(validateUi(r.ui)).toEqual(r.ui) // what we would save is valid
  })

  it('replace resets the CSS; a same-minute backup gets a distinct name', () => {
    const first = applyCssProposal(base, '#a{}', true, now)
    if (!first.ok) throw new Error(first.error)
    expect(first.ui.petCss).toBe('#a{}')
    const second = applyCssProposal(first.ui, '', true, now)
    if (!second.ok) throw new Error(second.error)
    expect(second.ui.petCss).toBe('')
    expect(second.ui.styles.map((x) => x.name)).toEqual(['AI 改动前 11-10 16:40', 'AI 改动前 11-10 16:40 (2)'])
  })

  it('refuses unsafe or oversized CSS', () => {
    for (const css of ['@import url(x);', '#a{background:url(http://x)}', '</style>', 'x'.repeat(4001)]) {
      expect(applyCssProposal(base, css, false, now).ok).toBe(false)
    }
    expect(applyCssProposal({ ...base, petCss: 'y'.repeat(19990) }, '#a { color: red; }', false, now).ok).toBe(false)
  })

  it('drops the oldest automatic backup when the list is full, but never user schemes', () => {
    const user = { name: '我的', consoleStyle: 'warm', inputStyle: 'pixel', bubbleStyle: 'pink', petCss: '', consoleCss: '' }
    const auto = (i: number) => ({ ...user, name: `AI 改动前 01-0${i} 10:00` })
    const full = { ...base, styles: [user, ...Array.from({ length: 29 }, (_, i) => auto(i % 9 + 1)).map((x, i) => ({ ...x, name: `AI 改动前 02-${String(i + 1).padStart(2, '0')} 10:00` }))] }
    const r = applyCssProposal(full, '#a{}', false, now)
    if (!r.ok) throw new Error(r.error)
    expect(r.ui.styles).toHaveLength(30)
    expect(r.ui.styles[0].name).toBe('我的')
    expect(r.ui.styles.some((x) => x.name === 'AI 改动前 02-01 10:00')).toBe(false)
    const allUser = { ...base, styles: Array.from({ length: 30 }, (_, i) => ({ ...user, name: `u${i}` })) }
    expect(applyCssProposal(allUser, '#a{}', false, now).ok).toBe(false)
  })
})

describe('nav_order', () => {
  it('reads the menu order and rejects bad values', () => {
    expect(parseUiConfig('nav_order: [prompt, model, prompt]').navOrder).toEqual(['prompt', 'model'])
    expect(parseUiConfig('theme: dark').navOrder).toEqual([])
    expect(() => parseUiConfig('nav_order: [1]')).toThrow(/nav_order/)
    expect(() => parseUiConfig('nav_order: prompt')).toThrow(/nav_order/)
  })
})
