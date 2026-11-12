import {
  withQuery,
  type BackendResult,
  type ChatMessage,
  type HistoryDay,
  type HistoryDeleteRequest,
  type HistoryPage
} from '../../shared/backend'

export const PAGE_SIZE = 50

export interface HistoryQuery {
  beforeId?: number
  limit?: number
  q?: string
  date?: string | null
}

export function fetchHistory(query: HistoryQuery): Promise<BackendResult<HistoryPage>> {
  const url = withQuery('/api/history', {
    before_id: query.beforeId,
    limit: query.limit ?? PAGE_SIZE,
    q: query.q?.trim() || undefined,
    date: query.date || undefined
  })
  return window.petConsole.backend<HistoryPage>('GET', url)
}

export function fetchHistoryDays(): Promise<BackendResult<HistoryDay[]>> {
  return window.petConsole.backend<HistoryDay[]>('GET', '/api/history/days')
}

export function deleteHistory(body: HistoryDeleteRequest): Promise<BackendResult<{ deleted: number }>> {
  return window.petConsole.backend<{ deleted: number }>('POST', '/api/history/delete', body)
}

/** How many messages "delete summarized" would remove when keeping the newest `keepRecent`. */
export function fetchSummarizedCount(keepRecent: number): Promise<BackendResult<{ deleted: number }>> {
  return window.petConsole.backend<{ deleted: number }>('GET', withQuery('/api/history/summarized-count', { keep_recent: keepRecent }))
}

const WEEKDAYS = ['日', '一', '二', '三', '四', '五', '六']

/** Local date key YYYY-MM-DD of a unix timestamp (seconds). */
export function dateKey(ts: number): string {
  const d = new Date(ts * 1000)
  const p = (n: number): string => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

export function fmtClock(ts: number): string {
  const d = new Date(ts * 1000)
  const p = (n: number): string => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}`
}

/** "MM-DD 星期X" for a YYYY-MM-DD string. */
export function dayLabel(date: string): string {
  const [y, m, d] = date.split('-').map(Number)
  const wd = WEEKDAYS[new Date(y, m - 1, d).getDay()]
  return `${date.slice(5)} 星期${wd}`
}

/** "YYYY-MM-DD 星期X" used for the in-list separators. */
export function separatorLabel(date: string): string {
  return `${date} ${dayLabel(date).split(' ')[1]}`
}

export interface YearGroup {
  year: string
  days: HistoryDay[]
}

/** Groups days (already newest first) by year, keeping order. */
export function groupByYear(days: HistoryDay[]): YearGroup[] {
  const groups: YearGroup[] = []
  for (const day of days) {
    const year = day.date.slice(0, 4)
    const last = groups[groups.length - 1]
    if (last && last.year === year) last.days.push(day)
    else groups.push({ year, days: [day] })
  }
  return groups
}

export interface Segment {
  text: string
  hit: boolean
}

/** Splits text into plain / matched segments (case-insensitive substring); no HTML involved. */
export function highlightSegments(text: string, q: string): Segment[] {
  const needle = q.trim()
  if (!needle) return [{ text, hit: false }]
  const lower = text.toLowerCase()
  const target = needle.toLowerCase()
  const out: Segment[] = []
  let pos = 0
  while (pos < text.length) {
    const idx = lower.indexOf(target, pos)
    if (idx < 0) break
    if (idx > pos) out.push({ text: text.slice(pos, idx), hit: false })
    out.push({ text: text.slice(idx, idx + target.length), hit: true })
    pos = idx + target.length
  }
  if (pos < text.length) out.push({ text: text.slice(pos), hit: false })
  return out.length ? out : [{ text, hit: false }]
}

export interface ListItem {
  key: string
  /** Set when a date separator is rendered before this message. */
  separator: string | null
  msg: ChatMessage
}

/** Oldest-first messages -> items with a date separator wherever the local day changes. */
export function withSeparators(messages: ChatMessage[]): ListItem[] {
  let prev: string | null = null
  return messages.map((msg) => {
    const day = dateKey(msg.ts)
    const separator = day !== prev ? separatorLabel(day) : null
    prev = day
    return { key: String(msg.id), separator, msg }
  })
}
