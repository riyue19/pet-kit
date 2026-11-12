import { reactive } from 'vue'
import { withQuery, type EventEntry, type EventsResponse, type InjectRequest } from '../../shared/backend'

interface Store {
  loading: boolean
  loaded: boolean
  offline: boolean
  error: string | null
  /** Ascending by seq (oldest first). */
  events: EventEntry[]
  lastSeq: number
  injectable: string[]
  /** Every event type the backend knows (the type filter's choices). */
  types: string[]
}

export const MAX_EVENTS = 500
/** Events still without a result are re-fetched for this long (seconds). */
export const PENDING_WINDOW_S = 60
const FULL_LIMIT = 200

export const eventsStore = reactive<Store>({
  loading: false,
  loaded: false,
  offline: false,
  error: null,
  events: [],
  lastSeq: 0,
  injectable: [],
  types: []
})

/** True when a local event has no result yet and is younger than the pending window. */
export function hasPending(events: EventEntry[], nowS: number = Date.now() / 1000): boolean {
  return events.some((e) => e.result === null && nowS - e.ts <= PENDING_WINDOW_S)
}

/** Merges incoming events into the list by seq (incoming wins), sorted ascending, capped at `max`. */
export function mergeEvents(current: EventEntry[], incoming: EventEntry[], max: number = MAX_EVENTS): EventEntry[] {
  const bySeq = new Map<number, EventEntry>()
  for (const e of current) bySeq.set(e.seq, e)
  for (const e of incoming) bySeq.set(e.seq, e)
  const all = [...bySeq.values()].sort((a, b) => a.seq - b.seq)
  return all.length > max ? all.slice(all.length - max) : all
}

/** Payload summary shown in the list row. */
export function summarize(e: EventEntry): string {
  const text = e.payload.text
  if (typeof text === 'string' && text) return text
  if (e.type === 'pet.interaction') return `点击 ${String(e.payload.area ?? '')}`
  return JSON.stringify(e.payload).slice(0, 80)
}

/**
 * One poll step. Incremental (after=last_seq) when nothing is pending; otherwise a full
 * limit=200 fetch so late results of already-seen events get updated. Never throws.
 */
export async function pollEvents(): Promise<void> {
  if (eventsStore.loading) return
  eventsStore.loading = true
  try {
    const full = !eventsStore.loaded || hasPending(eventsStore.events)
    const url = full
      ? withQuery('/api/events', { limit: FULL_LIMIT })
      : withQuery('/api/events', { after: eventsStore.lastSeq, limit: FULL_LIMIT })
    const res = await window.petConsole.backend<EventsResponse>('GET', url)
    eventsStore.offline = !res.ok && res.offline
    eventsStore.error = res.ok ? null : res.error
    if (res.ok && res.data.last_seq < eventsStore.lastSeq) {
      // The backend restarted (seq starts over): drop the old run's events and reload in full.
      eventsStore.events = []
      eventsStore.lastSeq = 0
      eventsStore.loaded = false
      return
    }
    if (res.ok) {
      eventsStore.events = mergeEvents(eventsStore.events, res.data.events)
      eventsStore.lastSeq = Math.max(eventsStore.lastSeq, res.data.last_seq)
      eventsStore.injectable = res.data.injectable
      eventsStore.types = res.data.types ?? []
      eventsStore.loaded = true
    }
  } catch (e) {
    eventsStore.error = e instanceof Error ? e.message : String(e)
  } finally {
    eventsStore.loading = false
  }
}

/** Injects an event; returns its id. Throws a user-facing message on failure. */
export async function injectEvent(body: InjectRequest): Promise<string> {
  const res = await window.petConsole.backend<{ id: string }>('POST', '/api/events', body)
  if (!res.ok) throw new Error(res.error)
  return res.data.id
}
