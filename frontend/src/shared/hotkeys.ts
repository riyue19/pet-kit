/** config/hotkeys.yaml (mirrors backend/core/hotkeys.py). Only declared combos are registered with the OS. */

export type HotkeyAction = 'builtin' | 'reaction' | 'llm'
export type BuiltinAction = 'toggle_visible' | 'toggle_mic' | 'toggle_camera' | 'stop_speaking' | 'open_console' | 'push_to_talk'
export const BUILTIN_ACTIONS: readonly BuiltinAction[] = ['toggle_visible', 'toggle_mic', 'toggle_camera', 'stop_speaking', 'open_console', 'push_to_talk']
export const BUILTIN_LABELS: Record<BuiltinAction, string> = {
  toggle_visible: '显示 / 隐藏桌宠',
  toggle_mic: '麦克风开关',
  toggle_camera: '摄像头开关',
  stop_speaking: '停止说话',
  open_console: '打开控制台',
  push_to_talk: '按住说话（松开就发送）'
}

export interface Hotkey {
  /** ^[a-z][a-z0-9_]{0,31}$, unique */
  id: string
  name: string
  /** Electron accelerator, e.g. "Ctrl+Alt+H" (see normalizeAccelerator) */
  keys: string
  enabled: boolean
  action: HotkeyAction
  builtin: BuiltinAction | null
  /** reaction: random line / expression label / motion label */
  lines: string[]
  expressions: string[]
  motions: string[]
  /** llm: what the pet is told happened */
  prompt: string
  /** 0..3600 */
  cooldown_seconds: number
}

export interface HotkeyStatus {
  id: string
  /** registered with the OS */
  ok: boolean
  /** why not: disabled / invalid combo / taken by another app / duplicate */
  error: string | null
}

export interface HotkeysState {
  hotkeys: Hotkey[]
  status: HotkeyStatus[]
  /** Path of config/hotkeys.yaml, for display. */
  file: string
  /**
   * Problems found reading the file (syntax error, or entries that were skipped). Saving from the console
   * rewrites the file from `hotkeys`, so skipped entries would be lost — the page must warn first.
   */
  error: string | null
}

export const HOTKEY_ID_RE = /^[a-z][a-z0-9_]{0,31}$/
export const MAX_HOTKEYS = 50
/** llm presses cost money; same floor as backend/core/hotkeys.py MIN_LLM_COOLDOWN. */
export const MIN_LLM_COOLDOWN = 3

const MODIFIERS = ['Ctrl', 'Alt', 'Shift', 'Super'] as const
const MODIFIER_ALIASES: Record<string, (typeof MODIFIERS)[number]> = {
  ctrl: 'Ctrl',
  control: 'Ctrl',
  commandorcontrol: 'Ctrl',
  cmdorctrl: 'Ctrl',
  alt: 'Alt',
  option: 'Alt',
  shift: 'Shift',
  super: 'Super',
  meta: 'Super',
  win: 'Super',
  cmd: 'Super',
  command: 'Super'
}
const NAMED_KEYS = [
  'Space', 'Tab', 'Enter', 'Backspace', 'Delete', 'Insert', 'Home', 'End', 'PageUp', 'PageDown',
  'Up', 'Down', 'Left', 'Right', 'Esc',
  'num0', 'num1', 'num2', 'num3', 'num4', 'num5', 'num6', 'num7', 'num8', 'num9',
  'numadd', 'numsub', 'nummult', 'numdiv', 'numdec'
]
const KEY_ALIASES: Record<string, string> = { return: 'Enter', escape: 'Esc', del: 'Delete', ins: 'Insert' }
const PUNCT = ['-', '=', '[', ']', ';', "'", ',', '.', '/', '\\', '`']

function normalizeKey(k: string): string | null {
  if (/^[a-z0-9]$/i.test(k)) return k.toUpperCase()
  if (/^f([1-9]|1[0-9]|2[0-4])$/i.test(k)) return k.toUpperCase()
  if (PUNCT.includes(k)) return k
  const lower = k.toLowerCase()
  if (KEY_ALIASES[lower]) return KEY_ALIASES[lower]
  return NAMED_KEYS.find((n) => n.toLowerCase() === lower) ?? null
}

/**
 * Canonical "Ctrl+Alt+H" form, or an error. Rules: known modifiers + exactly one key; plain keys need at
 * least one modifier other than Shift (so typing is never hijacked); F1–F24 may stand alone.
 */
export function normalizeAccelerator(raw: string): { ok: true; value: string } | { ok: false; error: string } {
  const parts = raw.split('+').map((p) => p.trim())
  // "Ctrl++" (the plus key) isn't supported; keep the grammar simple.
  if (parts.some((p) => !p)) return { ok: false, error: '组合键格式不对，例如 Ctrl+Alt+H' }
  const mods = new Set<(typeof MODIFIERS)[number]>()
  let key: string | null = null
  for (const p of parts) {
    const mod = MODIFIER_ALIASES[p.toLowerCase()]
    if (mod) {
      mods.add(mod)
      continue
    }
    if (key !== null) return { ok: false, error: '只能有一个主键（其余必须是 Ctrl / Alt / Shift / Win）' }
    key = normalizeKey(p)
    if (key === null) return { ok: false, error: `不支持的按键：${p}` }
  }
  if (key === null) return { ok: false, error: '缺少主键（不能只有修饰键）' }
  const isF = /^F\d+$/.test(key)
  if (!isF && !mods.has('Ctrl') && !mods.has('Alt') && !mods.has('Super')) {
    return { ok: false, error: '需要至少一个 Ctrl / Alt / Win，避免打字时误触（F1–F24 除外）' }
  }
  return { ok: true, value: [...MODIFIERS.filter((m) => mods.has(m)), key].join('+') }
}

/** Accelerator for a keydown in the console's recorder; null while only modifiers are held. */
export function acceleratorFromKeyEvent(e: {
  key: string
  code: string
  ctrlKey: boolean
  altKey: boolean
  shiftKey: boolean
  metaKey: boolean
}): string | null {
  if (['Control', 'Alt', 'Shift', 'Meta', 'OS', 'AltGraph'].includes(e.key)) return null
  let key: string | null = null
  const code = e.code
  if (/^Key[A-Z]$/.test(code)) key = code.slice(3)
  else if (/^Digit\d$/.test(code)) key = code.slice(5)
  else if (/^F\d{1,2}$/.test(code)) key = code
  else if (/^Numpad\d$/.test(code)) key = `num${code.slice(6)}`
  else {
    const map: Record<string, string> = {
      Space: 'Space', Tab: 'Tab', Enter: 'Enter', NumpadEnter: 'Enter', Backspace: 'Backspace', Delete: 'Delete',
      Insert: 'Insert', Home: 'Home', End: 'End', PageUp: 'PageUp', PageDown: 'PageDown', ArrowUp: 'Up',
      ArrowDown: 'Down', ArrowLeft: 'Left', ArrowRight: 'Right', Escape: 'Esc', Minus: '-', Equal: '=',
      BracketLeft: '[', BracketRight: ']', Semicolon: ';', Quote: "'", Comma: ',', Period: '.', Slash: '/',
      Backslash: '\\', Backquote: '`', NumpadAdd: 'numadd', NumpadSubtract: 'numsub',
      NumpadMultiply: 'nummult', NumpadDivide: 'numdiv', NumpadDecimal: 'numdec'
    }
    key = map[code] ?? null
  }
  if (!key) return null
  const mods = [e.ctrlKey && 'Ctrl', e.altKey && 'Alt', e.shiftKey && 'Shift', e.metaKey && 'Super'].filter(Boolean)
  return [...mods, key].join('+')
}

/**
 * Reading the file: each entry on its own, so one bad entry doesn't disable the rest (the backend does the
 * same). Returns the valid entries plus a message per skipped one.
 */
export function parseHotkeysLenient(raw: unknown): { hotkeys: Hotkey[]; skipped: string[] } {
  const hotkeys: Hotkey[] = []
  const skipped: string[] = []
  const items = Array.isArray(raw) ? raw : []
  items.forEach((item, i) => {
    try {
      // Validating against the accepted ones catches duplicate ids/combos too.
      const [h] = validateHotkeys([...hotkeys, item]).slice(-1)
      hotkeys.push(h)
    } catch (e) {
      skipped.push(`第 ${i + 1} 项：${e instanceof Error ? e.message : String(e)}`)
    }
  })
  return { hotkeys, skipped }
}

/** Validates a full config (console save). Returns the cleaned list or throws Error(中文). */
export function validateHotkeys(raw: unknown): Hotkey[] {
  if (!Array.isArray(raw)) throw new Error('hotkeys 必须是列表')
  if (raw.length > MAX_HOTKEYS) throw new Error(`最多 ${MAX_HOTKEYS} 个快捷键`)
  const seenIds = new Set<string>()
  const seenKeys = new Map<string, string>()
  return raw.map((item, i): Hotkey => {
    const o = (typeof item === 'object' && item !== null ? item : {}) as Record<string, unknown>
    const where = `第 ${i + 1} 个快捷键`
    const id = typeof o.id === 'string' ? o.id.trim() : ''
    if (!HOTKEY_ID_RE.test(id)) throw new Error(`${where}：id 只能用小写字母、数字、下划线，且以字母开头（≤32）`)
    if (seenIds.has(id)) throw new Error(`id 重复：${id}`)
    seenIds.add(id)
    const acc = normalizeAccelerator(typeof o.keys === 'string' ? o.keys : '')
    if (!acc.ok) throw new Error(`${id}：${acc.error}`)
    const enabled = o.enabled !== false
    if (enabled) {
      const other = seenKeys.get(acc.value)
      if (other) throw new Error(`${id} 和 ${other} 使用了同一个组合键 ${acc.value}`)
      seenKeys.set(acc.value, id)
    }
    const action = o.action
    if (action !== 'builtin' && action !== 'reaction' && action !== 'llm') {
      throw new Error(`${id}：action 必须是 builtin / reaction / llm`)
    }
    // Keep in step with backend/core/hotkeys.py (_text/_texts): numbers become text, blanks drop, extras truncate.
    const text = (v: unknown, len: number): string =>
      typeof v === 'string' || typeof v === 'number' ? String(v).trim().slice(0, len) : ''
    const strs = (v: unknown, max: number, len: number): string[] =>
      Array.isArray(v) ? v.map((x) => text(x, len)).filter((x) => x !== '').slice(0, max) : []
    const hk: Hotkey = {
      id,
      name: text(o.name, 32),
      keys: acc.value,
      enabled,
      action,
      builtin: BUILTIN_ACTIONS.includes(o.builtin as BuiltinAction) ? (o.builtin as BuiltinAction) : null,
      lines: strs(o.lines, 50, 200),
      expressions: strs(o.expressions, 20, 32),
      motions: strs(o.motions, 20, 32),
      prompt: text(o.prompt, 500),
      cooldown_seconds: typeof o.cooldown_seconds === 'number' && o.cooldown_seconds >= 0 && o.cooldown_seconds <= 3600 ? o.cooldown_seconds : 2
    }
    if (action === 'llm') hk.cooldown_seconds = Math.max(hk.cooldown_seconds, MIN_LLM_COOLDOWN)
    if (action === 'builtin' && !hk.builtin) throw new Error(`${id}：请选择内置动作`)
    if (action === 'llm' && !hk.prompt) throw new Error(`${id}：请填写交给 LLM 的提示`)
    if (action === 'reaction' && !hk.lines.length && !hk.expressions.length && !hk.motions.length) {
      throw new Error(`${id}：至少要有一句台词、一个表情或一个动作`)
    }
    return hk
  })
}
