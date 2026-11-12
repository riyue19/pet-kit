import { computed, reactive } from 'vue'
import { clone } from './util'
import { withQuery } from '../../shared/backend'
import type { MemoryConfig, MemoryItem, MemoryState, SummaryResult } from '../../shared/backend'

interface Store {
  loading: boolean
  loaded: boolean
  offline: boolean
  error: string | null
  config: MemoryConfig
  stats: MemoryState['stats']
  fetchCommand: string
  /** JSON of the last saved/loaded MemoryConfig. */
  snapshot: string
  items: MemoryItem[]
  itemsLoading: boolean
  itemsError: string | null
  query: string
  semantic: boolean
  page: number
  pageSize: number
}

export const PAGE_SIZES = [10, 20, 50]

const emptyConfig = (): MemoryConfig => ({
  enabled: true,
  summarize_every_turns: 20,
  recall_memories: 5,
  recall_messages: 3,
  min_similarity: 0.5,
  model_dir: '',
  db_path: ''
})

const emptyStats = (): MemoryState['stats'] => ({
  messages: 0,
  memories: 0,
  unsummarized_turns: 0,
  embed_ready: false,
  db_path: '',
  last_summary: null
})

export const mem = reactive<Store>({
  loading: false,
  loaded: false,
  offline: false,
  error: null,
  config: emptyConfig(),
  stats: emptyStats(),
  fetchCommand: '',
  snapshot: '',
  items: [],
  itemsLoading: false,
  itemsError: null,
  query: '',
  semantic: false,
  page: 1,
  pageSize: 20
})

function clampPage(): void {
  const max = Math.max(1, Math.ceil(mem.items.length / mem.pageSize))
  mem.page = Math.min(Math.max(1, mem.page), max)
}

export const pageItems = computed(() => mem.items.slice((mem.page - 1) * mem.pageSize, mem.page * mem.pageSize))


export const dirty = computed(() => mem.loaded && JSON.stringify(mem.config) !== mem.snapshot)

function applyStats(state: MemoryState): void {
  mem.stats = state.stats
  mem.fetchCommand = state.fetch_command
  if (!state.stats.embed_ready) mem.semantic = false
}

function apply(state: MemoryState): void {
  mem.config = clone(state.config)
  mem.snapshot = JSON.stringify(mem.config)
  applyStats(state)
  mem.loaded = true
}

/** Loads state; discards unsaved settings edits unless `keepEdits`. */
export async function loadMemory(keepEdits = false): Promise<void> {
  mem.loading = true
  try {
    const res = await window.petConsole.backend<MemoryState>('GET', '/api/memory')
    mem.offline = !res.ok && res.offline
    mem.error = res.ok ? null : res.error
    if (res.ok) {
      if (keepEdits && mem.loaded) applyStats(res.data)
      else apply(res.data)
    }
  } finally {
    mem.loading = false
  }
}

export function revertMemory(): void {
  mem.config = JSON.parse(mem.snapshot) as MemoryConfig
}

/** Throws a user-facing message on failure. Returns the backend notice (db moved/switched), if any. */
export async function saveMemory(): Promise<string | null> {
  const res = await window.petConsole.backend<MemoryState>('PUT', '/api/memory', clone(mem.config))
  if (!res.ok) throw new Error(res.error)
  apply(res.data)
  return res.data.notice
}

/** Throws on transport/HTTP failure; a failed summary is returned with ok=false. */
export async function summarizeNow(): Promise<SummaryResult> {
  const res = await window.petConsole.backend<SummaryResult>('POST', '/api/memory/summarize')
  if (!res.ok) throw new Error(res.error)
  return res.data
}

// Bumped per request; a slower, older response must not overwrite a newer search.
let itemsGeneration = 0

export async function loadItems(resetPage = false): Promise<void> {
  const gen = ++itemsGeneration
  mem.itemsLoading = true
  try {
    const semantic = mem.semantic && mem.stats.embed_ready && mem.query.trim() !== ''
    const path = withQuery('/api/memories', {
      q: mem.query.trim(),
      semantic: semantic ? 'true' : undefined,
      limit: 1000
    })
    const res = await window.petConsole.backend<MemoryItem[]>('GET', path)
    if (gen !== itemsGeneration) return
    if (res.ok) {
      mem.items = res.data
      mem.itemsError = null
      if (resetPage) mem.page = 1
      else clampPage()
    } else {
      mem.itemsError = res.error
    }
  } finally {
    if (gen === itemsGeneration) mem.itemsLoading = false
  }
}

export async function addItem(text: string, pinned: boolean): Promise<MemoryItem> {
  const res = await window.petConsole.backend<MemoryItem>('POST', '/api/memories', { text, pinned })
  if (!res.ok) throw new Error(res.error)
  mem.items = [res.data, ...mem.items]
  mem.page = 1
  mem.stats.memories += 1
  return res.data
}

export async function updateItem(id: number, patch: { text?: string; pinned?: boolean }): Promise<void> {
  const res = await window.petConsole.backend<MemoryItem>('PUT', '/api/memories', { id, ...patch })
  if (!res.ok) throw new Error(res.error)
  const keepScore = mem.items.find((i) => i.id === id)?.score ?? null
  mem.items = mem.items.map((i) => (i.id === id ? { ...res.data, score: res.data.score ?? keepScore } : i))
}

export async function deleteItem(id: number): Promise<void> {
  const res = await window.petConsole.backend<{ deleted: number }>('DELETE', '/api/memories', { id })
  if (!res.ok) throw new Error(res.error)
  mem.items = mem.items.filter((i) => i.id !== id)
  clampPage()
  if (res.data.deleted > 0) mem.stats.memories = Math.max(0, mem.stats.memories - 1)
}

function pad(n: number): string {
  return String(n).padStart(2, '0')
}

/** Unix seconds -> "YYYY-MM-DD HH:mm" (local time). */
export function formatTime(sec: number): string {
  const d = new Date(sec * 1000)
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

export function summaryText(s: SummaryResult): string {
  const when = s.at !== null ? `${formatTime(s.at)}：` : ''
  return `${when}处理 ${s.lines} 行，新增 ${s.added}、修改 ${s.updated}、删除 ${s.deleted}`
}
