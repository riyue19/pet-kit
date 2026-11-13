import { computed, reactive } from 'vue'
import { clone } from './util'
import type { StatusConfig, StatusHistory, StatusLive, StatusRecent, StatusStat, StatusState } from '../../shared/backend'

interface Store {
  loading: boolean
  loaded: boolean
  offline: boolean
  error: string | null
  form: StatusConfig
  /** JSON of the last saved/loaded StatusConfig. */
  snapshot: string
  /** The saved config the live numbers belong to. */
  saved: StatusConfig
  live: StatusLive[]
  recent: StatusRecent[]
  /** Limits of the form, from the backend (the defaults only stand in until it answers). */
  maxStats: number
  maxTiers: number
  maxKeepHistory: number
  /** Records kept in total (the backend's count; the history page below pages through them). */
  recentTotal: number
  history: { page: number; pageSize: number; items: StatusRecent[]; total: number; loading: boolean; error: string | null }
}

export const HISTORY_PAGE_SIZES = [10, 20, 50]
export const DEFAULT_KEEP_HISTORY = 50

export const AFFECTION_ID = 'affection'
export const STAT_ID_RE = /^[a-z][a-z0-9_]{0,23}$/
const COLORS = ['#8c6cff', '#4aa8ff', '#31c48d', '#f0a020', '#f2647a', '#b06cf0', '#20b8c8']

const empty = (): StatusConfig => ({ enabled: false, stats: [], keep_history: DEFAULT_KEEP_HISTORY })

export const status = reactive<Store>({
  loading: false,
  loaded: false,
  offline: false,
  error: null,
  form: empty(),
  snapshot: '',
  saved: empty(),
  live: [],
  recent: [],
  maxStats: 12,
  maxTiers: 20,
  maxKeepHistory: 100000,
  recentTotal: 0,
  history: { page: 1, pageSize: 20, items: [], total: 0, loading: false, error: null }
})

const serialize = (): string => JSON.stringify(status.form)

export const dirty = computed(() => status.loaded && serialize() !== status.snapshot)

function applyLive(s: StatusState): void {
  status.saved = clone(s.config)
  status.live = s.stats
  status.recent = s.recent
  status.maxStats = s.max_stats ?? status.maxStats
  status.maxTiers = s.max_tiers ?? status.maxTiers
  status.maxKeepHistory = s.max_keep_history ?? status.maxKeepHistory
  status.recentTotal = s.recent_total ?? s.recent.length
}

function apply(s: StatusState): void {
  status.form = clone(s.config)
  // An older backend has no keep_history; the form still shows the default.
  status.form.keep_history ??= DEFAULT_KEEP_HISTORY
  status.snapshot = serialize()
  status.loaded = true
  applyLive(s)
}

export async function loadStatus(): Promise<void> {
  status.loading = true
  try {
    const res = await window.petConsole.backend<StatusState>('GET', '/api/status')
    status.offline = !res.ok && res.offline
    status.error = res.ok ? null : res.error
    if (res.ok) apply(res.data)
  } finally {
    status.loading = false
  }
}

/** Re-reads only the live numbers; unsaved edits stay. */
export async function refreshLive(): Promise<void> {
  const res = await window.petConsole.backend<StatusState>('GET', '/api/status')
  if (res.ok) applyLive(res.data)
}

export function revertStatus(): void {
  if (status.snapshot) status.form = JSON.parse(status.snapshot) as StatusConfig
}

/** Saves the form. Throws a user-facing message on failure. */
export async function saveStatus(): Promise<void> {
  const res = await window.petConsole.backend<StatusState>('PUT', '/api/status', clone(status.form))
  if (!res.ok) throw new Error(res.error || '保存失败')
  apply(res.data)
}

/** Turns the status bar on or off at once, on top of the saved settings: unsaved edits in the form stay unsaved. */
export async function saveEnabled(on: boolean): Promise<void> {
  const res = await window.petConsole.backend<StatusState>('PUT', '/api/status', { ...clone(status.saved), enabled: on })
  if (!res.ok) throw new Error(res.error || '保存失败')
  applyLive(res.data)
  status.form.enabled = on
  if (status.snapshot) status.snapshot = JSON.stringify({ ...(JSON.parse(status.snapshot) as StatusConfig), enabled: on })
}

/** Sets a value by hand; null = back to the initial value. Throws a user-facing message on failure. */
export async function setValue(id: string, value: number | null): Promise<void> {
  const res = await window.petConsole.backend<StatusState>('POST', '/api/status/value', { id, value })
  if (!res.ok) throw new Error(res.error || '修改失败')
  applyLive(res.data)
}

export function newStat(): StatusStat {
  const ids = new Set(status.form.stats.map((s) => s.id))
  let n = 1
  while (ids.has(`stat_${n}`)) n++
  return {
    id: `stat_${n}`,
    name: '新状态',
    meaning: '',
    rule: '',
    min: 0,
    max: 100,
    initial: 0,
    max_up: 1,
    max_down: 1,
    daily_up: 0,
    color: COLORS[(status.form.stats.length - 1 + COLORS.length) % COLORS.length],
    tiers: []
  }
}

/** First problem with a stat in the form, or null. Mirrors backend/core/status_config.py. */
export function statError(s: StatusStat, all: StatusStat[]): string | null {
  if (!STAT_ID_RE.test(s.id)) return 'id 只能用小写字母、数字、下划线，字母开头，最多 24 个字符'
  if (all.filter((x) => x.id === s.id).length > 1) return 'id 和别的状态值重复了'
  if (!s.name.trim()) return '名称不能为空'
  if (!(s.min < s.max)) return '最小值必须小于最大值'
  if (s.initial < s.min || s.initial > s.max) return '初始值要在最小值和最大值之间'
  if (s.tiers.some((t) => !t.name.trim())) return '有区间没有填名称'
  if (new Set(s.tiers.map((t) => t.min)).size !== s.tiers.length) return '有两个区间的下限相同'
  return null
}

export const formError = computed(() => {
  for (const s of status.form.stats) {
    const e = statError(s, status.form.stats)
    if (e) return `${s.name || s.id}：${e}`
  }
  return null
})

/**
 * Loads the current history page. An older backend without /api/status/history falls back to the
 * newest few records that GET /api/status carries.
 */
export async function loadHistory(): Promise<void> {
  const h = status.history
  const maxPage = Math.max(1, Math.ceil(status.recentTotal / h.pageSize))
  if (h.page > maxPage) h.page = maxPage
  h.loading = true
  try {
    const offset = (h.page - 1) * h.pageSize
    const res = await window.petConsole.backend<StatusHistory>('GET', `/api/status/history?offset=${offset}&limit=${h.pageSize}`)
    if (res.ok) {
      h.items = res.data.items
      h.total = res.data.total
      h.error = null
    } else if (!res.offline && res.status === 404) {
      h.items = status.recent.slice(offset, offset + h.pageSize)
      h.total = status.recent.length
      h.error = null
    } else {
      h.error = res.error || '读取失败'
    }
  } finally {
    h.loading = false
  }
}
