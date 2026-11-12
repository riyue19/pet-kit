import { reactive } from 'vue'
import { withQuery, type LogEntry, type LogQuery, type LogsResponse } from '../../shared/backend'
import { mergeRows, toRows, type LogRow, type LogSource } from './logs-util'

export type SourceFilter = 'all' | LogSource

interface Store {
  rows: LogRow[]
  source: SourceFilter
  level: LogEntry['level']
  q: string
  eventId: string
  paused: boolean
  backendOffline: boolean
  backendError: string | null
  logDir: string | null
  loading: boolean
}

export const MAX_ROWS = 3000
const INITIAL_LIMIT = 500
const POLL_LIMIT = 1000
const POLL_MS = 2000

export const logs = reactive<Store>({
  rows: [],
  source: 'all',
  level: 'INFO',
  q: '',
  eventId: '',
  paused: false,
  backendOffline: false,
  backendError: null,
  logDir: null,
  loading: false
})

// seq counters are independent per source.
let lastSeqBackend = 0
let lastSeqFrontend = 0
/** Bumped whenever filters change so in-flight responses for old filters are dropped. */
let generation = 0
let timer: ReturnType<typeof setInterval> | null = null
let debounce: ReturnType<typeof setTimeout> | null = null
let busy = false

function baseQuery(): LogQuery {
  return {
    level: logs.level,
    q: logs.q.trim() || undefined,
    event_id: logs.eventId.trim() || undefined
  }
}

async function pullBackend(first: boolean, gen: number): Promise<void> {
  const after = first ? 0 : lastSeqBackend
  const b = baseQuery()
  const path = withQuery('/api/logs', {
    after: after || undefined,
    level: b.level,
    event_id: b.event_id,
    q: b.q,
    limit: first ? INITIAL_LIMIT : POLL_LIMIT
  })
  const res = await window.petConsole.backend<LogsResponse>('GET', path)
  if (gen !== generation) return
  if (!res.ok) {
    logs.backendOffline = res.offline
    logs.backendError = res.error
    return
  }
  logs.backendOffline = false
  logs.backendError = null
  const data = res.data
  if (data.log_dir) logs.logDir = data.log_dir
  if (data.last_seq < lastSeqBackend) {
    // Backend restarted: its seq counter started over. Drop old backend rows and reload fully.
    logs.rows = logs.rows.filter((r) => r.source !== 'backend')
    lastSeqBackend = 0
    await pullBackend(true, gen)
    return
  }
  lastSeqBackend = data.last_seq
  logs.rows = mergeRows(logs.rows, toRows(data.entries, 'backend'), MAX_ROWS)
}

async function pullFrontend(first: boolean, gen: number): Promise<void> {
  const query: LogQuery = { ...baseQuery(), limit: first ? INITIAL_LIMIT : POLL_LIMIT }
  if (!first && lastSeqFrontend) query.after = lastSeqFrontend
  const data = await window.petConsole.frontendLogs(query)
  if (gen !== generation) return
  lastSeqFrontend = data.last_seq
  logs.rows = mergeRows(logs.rows, toRows(data.entries, 'frontend'), MAX_ROWS)
}

async function pull(first: boolean): Promise<void> {
  if (busy && !first) return
  const gen = generation
  busy = true
  try {
    const tasks: Promise<void>[] = []
    if (logs.source !== 'frontend') tasks.push(pullBackend(first, gen).catch(() => undefined))
    if (logs.source !== 'backend') tasks.push(pullFrontend(first, gen).catch(() => undefined))
    await Promise.all(tasks)
  } finally {
    if (gen === generation) busy = false
  }
}

/** Clears the local list, resets both cursors and reloads with the current filters. */
export async function reloadLogs(): Promise<void> {
  generation++
  busy = false
  logs.rows = []
  lastSeqBackend = 0
  lastSeqFrontend = 0
  if (logs.source === 'frontend') {
    logs.backendOffline = false
    logs.backendError = null
  }
  logs.loading = true
  try {
    await pull(true)
  } finally {
    logs.loading = false
  }
}

/** Call after a filter changed. Keyword/event-id edits are debounced 300 ms. */
export function filtersChanged(debounced = false): void {
  if (debounce) clearTimeout(debounce)
  debounce = null
  if (!debounced) {
    void reloadLogs()
    return
  }
  debounce = setTimeout(() => {
    debounce = null
    void reloadLogs()
  }, 300)
}

/** Local clear only: keeps cursors so old lines do not come back. */
export function clearLocal(): void {
  logs.rows = []
}

export function startLogs(): void {
  stopLogs()
  void reloadLogs()
  timer = setInterval(() => {
    if (!logs.paused) void pull(false)
  }, POLL_MS)
}

export function stopLogs(): void {
  if (timer) clearInterval(timer)
  timer = null
  if (debounce) clearTimeout(debounce)
  debounce = null
}

/** Opens the log folder. Returns an error message, or '' on success. */
export async function openFolder(): Promise<string> {
  return window.petConsole.openLogFolder()
}
