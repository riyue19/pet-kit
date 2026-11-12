import { readFile, rename, rm, writeFile } from 'node:fs/promises'
import { parse, parseDocument } from 'yaml'
import { ConfigError } from './config'
import { parseHotkeysLenient, validateHotkeys, type Hotkey, type HotkeyStatus } from '../shared/hotkeys'

const HEADER = `# 全局快捷键（M5）。控制台「快捷键」页可视化编辑；保存后立即重新注册，无需重启。
# 只有这里声明的组合键会注册到系统，按键内容不会被记录（CLAUDE.md 4.2）。
#
# 每一项：
#   id                唯一标识，小写字母开头，可含数字和下划线
#   name              显示名称
#   keys              组合键，如 Ctrl+Alt+H（修饰键：Ctrl / Alt / Shift / Super(Win)；字母数字需带 Ctrl/Alt/Win，F1~F24 可单独用）
#   enabled           是否启用
#   action            builtin：本地动作（不经过后端）
#                     reaction：固定台词 + 表情/动作（不花钱）
#                     llm：把 prompt 交给 LLM 自由发挥（花钱，受 cooldown_seconds 限制）
#   builtin           toggle_visible 显示/隐藏桌宠 | toggle_mic 麦克风开关 | toggle_camera 摄像头开关 | stop_speaking 停止说话 | open_console 打开控制台
#                     | push_to_talk 按住说话：按住期间录音（麦克风常开关着也行），松开立即识别并发给桌宠
#   lines/expressions/motions   reaction 用：随机选一句台词 / 一个表情标签 / 一个动作标签
#   prompt            llm 用：告诉桌宠发生了什么，例如「（主人按下了快捷键，想听你夸夸他）」
#   cooldown_seconds  两次触发的最短间隔（秒）
`

/** Valid entries plus a description of anything skipped. Syntax errors throw ConfigError. */
export async function readHotkeysFile(file: string): Promise<{ hotkeys: Hotkey[]; skipped: string[] }> {
  let text: string
  try {
    text = await readFile(file, 'utf8')
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code === 'ENOENT') return { hotkeys: [], skipped: [] }
    throw e
  }
  let doc: unknown
  try {
    doc = parse(text)
  } catch (e) {
    throw new ConfigError(`${file}: YAML 语法错误：${e instanceof Error ? e.message : String(e)}`)
  }
  return parseHotkeysLenient((doc as { hotkeys?: unknown } | null)?.hotkeys ?? [])
}

/** Strict variant (tests, tools): throws if any entry is invalid. */
export async function loadHotkeys(file: string): Promise<Hotkey[]> {
  const { hotkeys, skipped } = await readHotkeysFile(file)
  if (skipped.length) throw new ConfigError(`${file}: ${skipped.join('；')}`)
  return hotkeys
}

function toYaml(h: Hotkey): Record<string, unknown> {
  // Only the fields that matter for the action, so the file stays readable.
  const o: Record<string, unknown> = { id: h.id, name: h.name, keys: h.keys, enabled: h.enabled, action: h.action }
  if (h.action === 'builtin') o.builtin = h.builtin
  if (h.action === 'reaction') Object.assign(o, { lines: h.lines, expressions: h.expressions, motions: h.motions })
  if (h.action === 'llm') o.prompt = h.prompt
  if (h.action !== 'builtin') o.cooldown_seconds = h.cooldown_seconds
  return o
}

/** Validates, writes atomically (keeping the header comments), returns the cleaned list. */
export async function saveHotkeys(file: string, raw: unknown): Promise<Hotkey[]> {
  const hotkeys = validateHotkeys(raw)
  let original = ''
  try {
    original = await readFile(file, 'utf8')
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code !== 'ENOENT') throw e
  }
  const doc = parseDocument(original || HEADER)
  if (doc.errors.length) throw new ConfigError(`${file}: YAML 语法错误：${doc.errors[0].message}`)
  if (!doc.contents) doc.contents = doc.createNode({}) as unknown as typeof doc.contents
  doc.set('hotkeys', hotkeys.map(toYaml))
  const text = doc.toString()
  const tmp = file + '.tmp'
  try {
    await writeFile(tmp, text, 'utf8')
    await rename(tmp, file)
  } catch (e) {
    await rm(tmp, { force: true }).catch(() => undefined)
    throw e
  }
  return hotkeys
}

/** The subset of Electron's globalShortcut used here (injectable for tests). */
export interface ShortcutApi {
  register(accelerator: string, cb: () => void): boolean
  unregister(accelerator: string): void
}

/**
 * Registers the enabled hotkeys with the OS. The OS reports only "this declared combo fired" —
 * no other keystrokes ever reach the app.
 */
export class HotkeyManager {
  private hotkeys: Hotkey[] = []
  private registered: string[] = []
  private status: HotkeyStatus[] = []
  private suspended = false
  private disposed = false

  constructor(
    private readonly api: ShortcutApi,
    private readonly onFire: (h: Hotkey) => void
  ) {}

  get current(): { hotkeys: Hotkey[]; status: HotkeyStatus[] } {
    return { hotkeys: this.hotkeys, status: this.status }
  }

  apply(hotkeys: Hotkey[]): HotkeyStatus[] {
    this.hotkeys = hotkeys
    this.register()
    return this.status
  }

  /** While the console records a new combo, existing ones must not fire. */
  setSuspended(suspended: boolean): void {
    if (suspended === this.suspended) return
    this.suspended = suspended
    this.register()
  }

  dispose(): void {
    this.disposed = true
    this.unregisterAll()
  }

  private unregisterAll(): void {
    for (const acc of this.registered) this.api.unregister(acc)
    this.registered = []
  }

  private register(): void {
    this.unregisterAll()
    if (this.disposed) return
    this.status = this.hotkeys.map((h): HotkeyStatus => {
      if (!h.enabled) return { id: h.id, ok: false, error: '已停用' }
      if (this.suspended) return { id: h.id, ok: false, error: '录制中，暂停' }
      let ok = false
      try {
        ok = this.api.register(h.keys, () => this.onFire(h))
      } catch (e) {
        return { id: h.id, ok: false, error: `组合键无效：${e instanceof Error ? e.message : String(e)}` }
      }
      if (!ok) return { id: h.id, ok: false, error: '被其他软件占用，换一个组合键试试' }
      this.registered.push(h.keys)
      return { id: h.id, ok: true, error: null }
    })
    const failed = this.status.filter((s) => !s.ok && s.error !== '已停用' && !this.suspended)
    if (failed.length) console.warn('[hotkeys] 以下快捷键未注册：', failed.map((s) => `${s.id}（${s.error}）`).join('，'))
  }
}
