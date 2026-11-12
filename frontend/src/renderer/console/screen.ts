import { computed, reactive } from 'vue'
import { clone } from './util'
import type {
  ChatCadence,
  EnvironmentConfig,
  PrivacyConfig,
  ProactiveConfig,
  ScreenCadence,
  ScreenConfig,
  ScreenLookResult,
  ScreenSave,
  ScreenState,
  WeatherResult
} from '../../shared/backend'

export type ScreenStatus = ScreenState['status']

interface Store {
  loading: boolean
  loaded: boolean
  offline: boolean
  error: string | null
  screen: ScreenConfig
  proactive: ProactiveConfig
  labels: { expressions: string[]; motions: string[] }
  chatCadences: Record<string, ChatCadence>
  environment: EnvironmentConfig
  privacy: PrivacyConfig
  cadences: Record<string, ScreenCadence>
  status: ScreenStatus
  /** JSON of the last saved/loaded ScreenSave. */
  snapshot: string
}

const emptyStatus = (): ScreenStatus => ({
  foreground: null,
  spoken_last_hour: 0,
  looks_last_hour: 0,
  last_comment_at: null,
  last_comment_app: null,
  last_comment_text: null,
  vision_profile: null,
  pet_connected: false,
  system_idle_seconds: null,
  last_proactive_at: null,
  last_proactive_kind: null,
  last_proactive_text: null
})

export const screenState = reactive<Store>({
  loading: false,
  loaded: false,
  offline: false,
  error: null,
  screen: { enabled: false, mode: 'normal', allow_fullscreen: false, quiet_after_user_seconds: 30, poll_seconds: 2 },
  proactive: { enabled: false, mode: 'normal', idle_chat: true, idle_after_minutes: 20, welcome_back: true, away_minutes: 10, greetings: true, idle_action: false, idle_action_minutes: 20, idle_action_motions: [], idle_action_expressions: [] },
  labels: { expressions: [], motions: [] },
  chatCadences: {},
  environment: { city: '', weather: true },
  privacy: { block_processes: [], block_titles: [], quiet_processes: [], quiet_titles: [] },
  cadences: {},
  status: emptyStatus(),
  snapshot: ''
})


function payload(): ScreenSave {
  return { screen: screenState.screen, proactive: screenState.proactive, environment: screenState.environment, privacy: screenState.privacy }
}

function serialize(): string {
  return JSON.stringify(payload())
}

export const dirty = computed(() => screenState.loaded && serialize() !== screenState.snapshot)

/** True when the saved (not the edited) screen.enabled is false. Used to confirm turning it on. */
export function enablingScreen(): boolean {
  if (!screenState.snapshot) return false
  const saved = JSON.parse(screenState.snapshot) as ScreenSave
  return !saved.screen.enabled && screenState.screen.enabled
}

/** True when the saved proactive.enabled is false and the edit turns it on. */
export function enablingProactive(): boolean {
  if (!screenState.snapshot) return false
  const saved = JSON.parse(screenState.snapshot) as ScreenSave
  return !saved.proactive.enabled && screenState.proactive.enabled
}

/** City as last saved (the weather test queries the saved city). */
export function cityDirty(): boolean {
  if (!screenState.snapshot) return false
  const saved = JSON.parse(screenState.snapshot) as ScreenSave
  return saved.environment.city !== screenState.environment.city
}

function apply(state: ScreenState): void {
  screenState.screen = clone(state.screen)
  screenState.proactive = clone(state.proactive)
  screenState.labels = clone(state.labels ?? { expressions: [], motions: [] })
  screenState.chatCadences = clone(state.chat_cadences)
  screenState.environment = clone(state.environment)
  screenState.privacy = clone(state.privacy)
  screenState.cadences = clone(state.cadences)
  screenState.status = clone(state.status)
  screenState.snapshot = serialize()
  screenState.loaded = true
}

export async function loadScreen(): Promise<void> {
  screenState.loading = true
  try {
    const res = await window.petConsole.backend<ScreenState>('GET', '/api/screen')
    screenState.offline = !res.ok && res.offline
    screenState.error = res.ok ? null : res.error
    if (res.ok) apply(res.data)
  } finally {
    screenState.loading = false
  }
}

/** Re-fetches only `status`; unsaved edits are kept. Failures are ignored (next tick retries). */
export async function refreshStatus(): Promise<void> {
  if (!screenState.loaded) return
  const res = await window.petConsole.backend<ScreenState>('GET', '/api/screen')
  if (res.ok) {
    screenState.offline = false
    screenState.status = clone(res.data.status)
  } else if (res.offline) {
    screenState.offline = true
    screenState.error = res.error
  }
}

export function revertScreen(): void {
  void loadScreen()
}

/** Throws a user-facing message on failure. */
export async function saveScreen(): Promise<void> {
  const res = await window.petConsole.backend<ScreenState>('PUT', '/api/screen', clone(payload()))
  if (!res.ok) throw new Error(res.error)
  apply(res.data)
}

export async function lookOnce(delaySeconds: number): Promise<ScreenLookResult> {
  const res = await window.petConsole.backend<ScreenLookResult>('POST', '/api/screen/look', { delay_seconds: delaySeconds })
  if (!res.ok) throw new Error(res.error)
  return res.data
}

export async function queryWeather(): Promise<WeatherResult> {
  const res = await window.petConsole.backend<WeatherResult>('POST', '/api/screen/weather')
  if (!res.ok) throw new Error(res.error)
  return res.data
}

function span(seconds: number): string {
  return seconds >= 60 ? `${Math.round(seconds / 60)} 分钟` : `${seconds} 秒`
}

export function cadenceText(c: ScreenCadence | undefined): string {
  if (!c) return ''
  return `停留 ${c.dwell_seconds} 秒后看一眼；同一内容 ${span(c.per_content_seconds)}内不重复；两次主动说话至少隔 ${span(c.min_gap_seconds)}；每小时最多 ${c.max_per_hour} 次`
}

export function chatCadenceText(c: ChatCadence | undefined): string {
  if (!c) return ''
  return `两次主动说话至少隔 ${span(c.min_gap_seconds)}；每小时最多 ${c.max_per_hour} 次；空闲搭话至少隔 ${span(c.idle_repeat_seconds)}`
}

export function formatTime(ts: number): string {
  const d = new Date(ts * 1000)
  const p = (n: number): string => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}`
}
