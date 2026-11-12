import { reactive, ref } from 'vue'
import type { BackendMethod, FishingCommand, FishingCommandResult, FishingSettings, FishingSimulation, FishingSnapshot } from '../../shared/backend'

interface Store {
  loading: boolean
  loaded: boolean
  offline: boolean
  error: string | null
  state: FishingSnapshot | null
  /** A write request is in flight; only one at a time. */
  busy: boolean
}

export const fishing = reactive<Store>({
  loading: false,
  loaded: false,
  offline: false,
  error: null,
  state: null,
  busy: false
})

export async function loadFishing(): Promise<void> {
  fishing.loading = true
  try {
    const res = await window.petConsole.backend<FishingSnapshot>('GET', '/api/fishing')
    fishing.offline = !res.ok && res.offline
    fishing.error = res.ok ? null : res.error
    if (res.ok) {
      fishing.state = res.data
      fishing.loaded = true
    }
  } catch (e) {
    fishing.error = e instanceof Error ? e.message : String(e)
  } finally {
    fishing.loading = false
  }
}

/** One write request at a time; throws a user-readable Error on failure. */
async function write<T>(method: BackendMethod, path: string, body?: unknown): Promise<T> {
  if (fishing.busy) throw new Error('上一个操作还没完成，请稍等')
  fishing.busy = true
  try {
    const res = await window.petConsole.backend<T>(method, path, body)
    if (!res.ok) throw new Error(res.error || '操作失败')
    return res.data
  } finally {
    fishing.busy = false
  }
}

export async function fishingCommand(cmd: FishingCommand, args: string[] = [], confirmed = false): Promise<FishingCommandResult> {
  const data = await write<FishingCommandResult>('POST', '/api/fishing/command', { cmd, args, confirmed })
  fishing.state = data.state
  return data
}

export async function saveFishingSettings(patch: Partial<FishingSettings>): Promise<void> {
  fishing.state = await write<FishingSnapshot>('PUT', '/api/fishing/settings', patch)
}

/** Which part of the fishing page is open; kept while the console is open (the lobby does not reset it). */
export const fishingView = ref<'scene' | 'manage'>('scene')

/** "3 分 20 秒" / "1 小时 05 分". */
export function waitText(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  if (h > 0) return `${h} 小时 ${String(m).padStart(2, '0')} 分`
  return m > 0 ? `${m} 分 ${String(s % 60).padStart(2, '0')} 秒` : `${s} 秒`
}

export async function simulateFishing(): Promise<FishingSimulation> {
  return write<FishingSimulation>('POST', '/api/fishing/simulate')
}
