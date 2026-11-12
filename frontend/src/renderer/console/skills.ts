import type { ConsoleBridge } from '../../shared/ipc'

// Not `window.petConsole`: the node tsconfig (which compiles the tests) has no DOM lib.
const bridge = (): ConsoleBridge => (globalThis as unknown as { petConsole: ConsoleBridge }).petConsole

export interface SkillTarget {
  label: string
  exe: string | null
  args: string[]
  url: string | null
  search_url: string | null
  credential: string | null
}

export interface SkillEntry {
  enabled: boolean
  confirm: boolean
  targets: Record<string, SkillTarget>
}

/** What PUT /api/skills accepts (no `available`). */
export interface SkillsConfig {
  enabled: boolean
  confirm_timeout_seconds: number
  skills: Record<string, SkillEntry>
}

export interface SkillsState extends SkillsConfig {
  available: string[]
  builtin?: string[]
}

export interface InstalledApp {
  name: string
  exe: string
}

/** Titles for the launchable skills and their built-in order on the page. The backend's list (`available`) is the truth. */
export const SKILL_TITLES: Record<string, string> = {
  open_app: '打开程序',
  play_music: '放音乐',
  open_game: '开游戏'
}
/** Titles and descriptions of the built-in actions (no targets, only on/off and confirmation). Which ones exist comes from the backend (`builtin`). */
export const BUILTIN_INFO: Record<string, { title: string; desc: string }> = {
  show_code: { title: '代码窗口', desc: '你让它写代码、脚本时，把代码显示在桌面上的一个小窗口里（可以复制）。' },
  set_style: { title: '修改桌宠外观', desc: '你让它改气泡、输入框的颜色或样式时，它给出一个预览，由你决定保留还是还原。' },
  look_camera: { title: '用摄像头看一眼', desc: '你说「看看我」时抽一帧摄像头画面。只有摄像头开着并在工作时才会用到。' },
  invite_game: { title: '邀请玩小游戏', desc: '你说无聊、想玩时弹窗邀请你玩一局。它不请自来的邀请另由「小游戏」页的开关控制。' }
}

/** Names of the built-in actions: the backend's list; with an older backend, the ones this page knows. */
export const builtinNames = (state: SkillsState): string[] => state.builtin ?? Object.keys(BUILTIN_INFO)
/** Names of the launchable skills, in the page's order: the backend's list plus anything else in the file. */
export function skillNames(state: SkillsState): string[] {
  const builtin = new Set(builtinNames(state))
  const known = state.available.length ? state.available : Object.keys(SKILL_TITLES)
  return [...new Set([...known, ...Object.keys(state.skills)])].filter((n) => !builtin.has(n))
}

export const TARGET_ID_RE = /^[a-z][a-z0-9_]{0,31}$/

/** Derives a unique target id from an exe path. */
export function makeTargetId(exe: string, existing: Iterable<string>): string {
  const taken = new Set(existing)
  const file = exe.split(/[\\/]/).pop() ?? ''
  const dot = file.lastIndexOf('.')
  const stem = dot > 0 ? file.slice(0, dot) : file
  let id = stem
    .toLowerCase()
    .replace(/[^a-z0-9_]/g, '_')
    .replace(/_+/g, '_')
    .replace(/^_+|_+$/g, '')
  if (id === '' || !/^[a-z]/.test(id)) id = `app_${id}`.replace(/_+$/, '')
  id = id.slice(0, 32).replace(/_+$/, '') || 'app'
  if (!taken.has(id)) return id
  for (let n = 2; ; n++) {
    const suffix = `_${n}`
    const cand = id.slice(0, 32 - suffix.length) + suffix
    if (!taken.has(cand)) return cand
  }
}

/** First validation error in Chinese, or null. */
export function validateSkills(cfg: SkillsConfig): string | null {
  if (!(cfg.confirm_timeout_seconds >= 5 && cfg.confirm_timeout_seconds <= 300)) return '确认等待时间必须在 5 到 300 秒之间'
  for (const [name, skill] of Object.entries(cfg.skills)) {
    const title = SKILL_TITLES[name] ?? name
    for (const [id, t] of Object.entries(skill.targets)) {
      const where = `「${title}」的目标 ${id || '（未填写 id）'}`
      if (!TARGET_ID_RE.test(id)) return `${where}：id 只能用小写字母、数字、下划线，以字母开头，最长 32 个字符`
      if (!t.label || t.label.trim() === '') return `${where}：名称不能为空`
      if (t.label.length > 32) return `${where}：名称最长 32 个字符`
      const hasExe = !!t.exe && t.exe.trim() !== ''
      const hasUrl = !!t.url && t.url.trim() !== ''
      if (hasExe === hasUrl) return `${where}：程序路径和 URL 必须填写其中一个`
      if (t.search_url && t.search_url.trim() !== '' && !t.search_url.includes('{query}')) return `${where}：搜索地址必须包含 {query}`
      if (t.credential && t.credential.trim() !== '' && !t.args.some((a) => a.includes('{secret}'))) {
        return `${where}：设置了凭据，参数里必须包含 {secret}`
      }
    }
  }
  return null
}

export async function loadSkills(): Promise<SkillsState> {
  const res = await bridge().backend<SkillsState>('GET', '/api/skills')
  if (!res.ok) throw Object.assign(new Error(res.error), { offline: res.offline === true })
  return res.data
}

export async function saveSkills(cfg: SkillsConfig): Promise<SkillsState> {
  const body: SkillsConfig = {
    enabled: cfg.enabled,
    confirm_timeout_seconds: cfg.confirm_timeout_seconds,
    skills: JSON.parse(JSON.stringify(cfg.skills)) as Record<string, SkillEntry>
  }
  const res = await bridge().backend<SkillsState>('PUT', '/api/skills', body)
  if (!res.ok) throw new Error(res.error)
  return res.data
}

export async function loadInstalled(): Promise<InstalledApp[]> {
  const res = await bridge().backend<InstalledApp[]>('GET', '/api/skills/installed')
  if (!res.ok) throw new Error(res.error)
  return res.data
}
