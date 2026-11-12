import { readFile, rename, rm, writeFile } from 'node:fs/promises'
import { parse, parseDocument } from 'yaml'
import { ConfigError } from './config'
import type { UiSettings } from '../shared/ipc'
import {
  BUBBLE_STYLES,
  CONSOLE_STYLES,
  CUSTOM_CSS_MAX_CHARS,
  INPUT_STYLES,
  MAX_SCHEMES,
  MAX_SCHEME_NAME_CHARS,
  isPreset,
  isSafeAiCss,
  type StyleFields,
  type StylePreset
} from '../shared/ui-style'

const DEFAULT_STYLE_FIELDS: StyleFields = {
  consoleStyle: 'default',
  inputStyle: 'default',
  bubbleStyle: 'default',
  petCss: '',
  consoleCss: ''
}

export const DEFAULT_UI: UiSettings = {
  theme: 'auto',
  accent: '#8c6cff',
  fontSize: 14,
  ...DEFAULT_STYLE_FIELDS,
  styles: [],
  navOrder: []
}
export const FONT_SIZE_RANGE = { min: 12, max: 20 }

type Obj = Record<string, unknown>
const isObj = (v: unknown): v is Obj => typeof v === 'object' && v !== null && !Array.isArray(v)

/** Reads the five look fields (camelCase or snake_case keys) from `v` into `into`; absent/null keeps the value. */
function readStyleFields<T extends StyleFields>(v: Obj, into: T, at: string): T {
  const preset = (snake: string, camel: 'consoleStyle' | 'inputStyle' | 'bubbleStyle', list: readonly StylePreset[]): void => {
    const val = v[camel] ?? v[snake]
    if (val === undefined || val === null) return
    if (!isPreset(list, val)) throw new ConfigError(`${at}${snake}: must be one of ${list.map((p) => p.id).join(', ')}`)
    into[camel] = val as T[typeof camel]
  }
  preset('console_style', 'consoleStyle', CONSOLE_STYLES)
  preset('input_style', 'inputStyle', INPUT_STYLES)
  preset('bubble_style', 'bubbleStyle', BUBBLE_STYLES)
  const css = (snake: string, camel: 'petCss' | 'consoleCss'): void => {
    const val = v[camel] ?? v[snake]
    if (val === undefined || val === null) return
    if (typeof val !== 'string') throw new ConfigError(`${at}${snake}: must be a string`)
    if (val.length > CUSTOM_CSS_MAX_CHARS) throw new ConfigError(`${at}${snake}: at most ${CUSTOM_CSS_MAX_CHARS} characters`)
    into[camel] = val as T[typeof camel]
  }
  css('pet_css', 'petCss')
  css('console_css', 'consoleCss')
  return into
}

export function validateUi(v: unknown): UiSettings {
  if (!isObj(v)) throw new ConfigError('<root>: must be a mapping')
  const ui: UiSettings = { ...DEFAULT_UI, styles: [], navOrder: [] }
  if (v.theme !== undefined) {
    if (v.theme !== 'auto' && v.theme !== 'light' && v.theme !== 'dark') {
      throw new ConfigError('theme: must be "auto", "light" or "dark"')
    }
    ui.theme = v.theme
  }
  if (v.accent !== undefined) {
    if (typeof v.accent !== 'string' || !/^#[0-9a-fA-F]{6}$/.test(v.accent)) {
      throw new ConfigError('accent: must be a color like "#8c6cff"')
    }
    ui.accent = v.accent.toLowerCase()
  }
  const size = v.fontSize ?? v.font_size
  if (size !== undefined) {
    const { min, max } = FONT_SIZE_RANGE
    if (typeof size !== 'number' || !Number.isInteger(size) || size < min || size > max) {
      throw new ConfigError(`font_size: must be an integer in [${min}, ${max}]`)
    }
    ui.fontSize = size
  }
  readStyleFields(v, ui, '')
  if (v.styles !== undefined && v.styles !== null) {
    if (!Array.isArray(v.styles)) throw new ConfigError('styles: must be a list')
    if (v.styles.length > MAX_SCHEMES) throw new ConfigError(`styles: at most ${MAX_SCHEMES} schemes`)
    const seen = new Set<string>()
    ui.styles = v.styles.map((raw, i) => {
      const at = `styles[${i}]`
      if (!isObj(raw)) throw new ConfigError(`${at}: must be a mapping`)
      const name = typeof raw.name === 'string' ? raw.name.trim() : ''
      if (!name || name.length > MAX_SCHEME_NAME_CHARS) {
        throw new ConfigError(`${at}.name: 1-${MAX_SCHEME_NAME_CHARS} characters`)
      }
      if (seen.has(name)) throw new ConfigError(`${at}.name: duplicate scheme name "${name}"`)
      seen.add(name)
      return { name, ...readStyleFields(raw, { ...DEFAULT_STYLE_FIELDS }, `${at}.`) }
    })
  }
  const nav = v.navOrder ?? v.nav_order
  if (nav !== undefined && nav !== null) {
    if (!Array.isArray(nav) || nav.length > 40 || nav.some((k) => typeof k !== 'string' || !/^[a-z]{1,20}$/.test(k))) {
      throw new ConfigError('nav_order: must be a list of page names')
    }
    ui.navOrder = [...new Set(nav as string[])]
  }
  return ui
}

const AI_BACKUP_PREFIX = 'AI 改动前 '

/**
 * The pet proposed CSS for itself. Returns the settings to save if the user keeps it: the CSS is appended to the
 * current pet CSS (or replaces it), and the look from before the change is stored as a scheme so it can be
 * restored from the console. Old automatic backups are dropped first when the scheme list is full.
 */
export function applyCssProposal(
  ui: UiSettings,
  css: string,
  replace: boolean,
  now: Date
): { ok: true; ui: UiSettings } | { ok: false; error: string } {
  if (!isSafeAiCss(css)) return { ok: false, error: 'CSS 含有不允许的内容或太长' }
  const petCss = replace ? css : [ui.petCss.trim(), css.trim()].filter(Boolean).join('\n\n')
  if (petCss.length > CUSTOM_CSS_MAX_CHARS) return { ok: false, error: '桌宠 CSS 已经太长，请先在控制台清理' }
  const p = (n: number): string => String(n).padStart(2, '0')
  const stamp = `${p(now.getMonth() + 1)}-${p(now.getDate())} ${p(now.getHours())}:${p(now.getMinutes())}`
  const taken = new Set(ui.styles.map((x) => x.name))
  let name = `${AI_BACKUP_PREFIX}${stamp}`
  for (let n = 2; taken.has(name); n++) name = `${AI_BACKUP_PREFIX}${stamp} (${n})`
  const { consoleStyle, inputStyle, bubbleStyle, petCss: oldPetCss, consoleCss } = ui
  let styles = [...ui.styles]
  while (styles.length >= MAX_SCHEMES) {
    const i = styles.findIndex((x) => x.name.startsWith(AI_BACKUP_PREFIX))
    if (i < 0) return { ok: false, error: '样式方案已满，请先在控制台删除一些' }
    styles = styles.filter((_, j) => j !== i)
  }
  styles.push({ name, consoleStyle, inputStyle, bubbleStyle, petCss: oldPetCss, consoleCss })
  return { ok: true, ui: { ...ui, petCss, styles } }
}

export function parseUiConfig(text: string): UiSettings {
  let doc: unknown
  try {
    doc = parse(text)
  } catch (e) {
    throw new ConfigError(`YAML syntax error: ${e instanceof Error ? e.message : String(e)}`)
  }
  if (doc === undefined || doc === null) return { ...DEFAULT_UI }
  return validateUi(doc)
}

export async function loadUiConfig(filePath: string): Promise<UiSettings> {
  try {
    return parseUiConfig(await readFile(filePath, 'utf8'))
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code === 'ENOENT') return { ...DEFAULT_UI }
    throw e
  }
}

export async function saveUiConfig(filePath: string, raw: unknown): Promise<UiSettings> {
  const ui = validateUi(raw)
  let original = ''
  try {
    original = await readFile(filePath, 'utf8')
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code !== 'ENOENT') throw e
  }
  const doc = parseDocument(original)
  if (doc.errors.length > 0) throw new ConfigError(`YAML syntax error: ${doc.errors[0].message}`)
  doc.set('theme', ui.theme)
  doc.set('accent', ui.accent)
  doc.set('font_size', ui.fontSize)
  doc.set('console_style', ui.consoleStyle)
  doc.set('input_style', ui.inputStyle)
  doc.set('bubble_style', ui.bubbleStyle)
  doc.set('pet_css', ui.petCss)
  doc.set('console_css', ui.consoleCss)
  doc.set('nav_order', ui.navOrder)
  doc.set(
    'styles',
    ui.styles.map((x) => ({
      name: x.name,
      console_style: x.consoleStyle,
      input_style: x.inputStyle,
      bubble_style: x.bubbleStyle,
      pet_css: x.petCss,
      console_css: x.consoleCss
    }))
  )
  const tmp = filePath + '.tmp'
  try {
    await writeFile(tmp, doc.toString(), 'utf8')
    await rename(tmp, filePath)
  } catch (e) {
    await rm(tmp, { force: true }).catch(() => undefined)
    throw e
  }
  return ui
}
