import type { LogEntry } from '../../shared/backend'

export type LogSource = 'backend' | 'frontend'

/** A log entry tagged with its origin; seq is only unique per source. */
export interface LogRow extends LogEntry {
  source: LogSource
  key: string
}

export function toRows(entries: LogEntry[], source: LogSource): LogRow[] {
  return entries.map((e) => ({ ...e, source, key: `${source}:${e.seq}` }))
}

/** Merges incoming rows into existing ones, dedupes by key, sorts by ts (then source/seq), keeps the newest `max`. */
export function mergeRows(existing: LogRow[], incoming: LogRow[], max: number): LogRow[] {
  if (incoming.length === 0) return existing
  const seen = new Set(existing.map((r) => r.key))
  const fresh = incoming.filter((r) => {
    if (seen.has(r.key)) return false
    seen.add(r.key)
    return true
  })
  if (fresh.length === 0) return existing
  const all = existing.concat(fresh)
  all.sort((a, b) => a.ts - b.ts || (a.source === b.source ? a.seq - b.seq : a.source < b.source ? -1 : 1))
  return all.length > max ? all.slice(all.length - max) : all
}

export function countLevels(rows: LogEntry[]): { error: number; warning: number } {
  let error = 0
  let warning = 0
  for (const r of rows) {
    if (r.level === 'ERROR' || r.level === 'CRITICAL') error++
    else if (r.level === 'WARNING') warning++
  }
  return { error, warning }
}

const pad = (n: number, w = 2): string => String(n).padStart(w, '0')

/** HH:mm:ss.SSS in local time. */
export function formatTime(tsSeconds: number): string {
  const d = new Date(tsSeconds * 1000)
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}.${pad(d.getMilliseconds(), 3)}`
}

export function levelTagType(level: LogEntry['level']): 'default' | 'info' | 'warning' | 'error' {
  if (level === 'ERROR' || level === 'CRITICAL') return 'error'
  if (level === 'WARNING') return 'warning'
  if (level === 'INFO') return 'info'
  return 'default'
}
