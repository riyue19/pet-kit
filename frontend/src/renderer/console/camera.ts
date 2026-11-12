import { reactive } from 'vue'
import { clone } from './util'

export interface CameraConfig {
  enabled: boolean
  device_index: number
  check_seconds: number
  away_after_seconds: number
  greet_on_return: boolean
  greet_min_away_minutes: number
  model_dir: string
}

export interface CameraStatus {
  camera: CameraConfig
  open: boolean
  present: boolean | null
  error: string | null
  model_ready: boolean
  vision_profile: string | null
  last_greeting_at: number | null
  last_greeting_text: string | null
}

export interface CameraLookResult {
  scheduled: boolean
  error: string | null
}

export type CameraRuntime = Omit<CameraStatus, 'camera'>

interface Store {
  loaded: boolean
  offline: boolean
  error: string | null
  /** Editable form; polling never touches it. */
  form: CameraConfig
  /** JSON of the last saved/loaded config. */
  snapshot: string
  runtime: CameraRuntime
}


export const cameraState = reactive<Store>({
  loaded: false,
  offline: false,
  error: null,
  form: {
    enabled: false,
    device_index: 0,
    check_seconds: 2,
    away_after_seconds: 60,
    greet_on_return: true,
    greet_min_away_minutes: 10,
    model_dir: ''
  },
  snapshot: '',
  runtime: {
    open: false,
    present: null,
    error: null,
    model_ready: true,
    vision_profile: null,
    last_greeting_at: null,
    last_greeting_text: null
  }
})

export function cameraDirty(): boolean {
  return cameraState.loaded && JSON.stringify(cameraState.form) !== cameraState.snapshot
}

export function revertCamera(): void {
  if (cameraState.snapshot) cameraState.form = JSON.parse(cameraState.snapshot) as typeof cameraState.form
}

function applyRuntime(s: CameraStatus): void {
  const { camera: _camera, ...rest } = s
  cameraState.runtime = clone(rest)
}

function applyAll(s: CameraStatus): void {
  cameraState.form = clone(s.camera)
  cameraState.snapshot = JSON.stringify(s.camera)
  applyRuntime(s)
  cameraState.loaded = true
}

/** Initial load / reload: replaces the form. */
export async function loadCamera(): Promise<void> {
  const res = await window.petConsole.backend<CameraStatus>('GET', '/api/camera')
  cameraState.offline = !res.ok && res.offline
  cameraState.error = res.ok ? null : res.error
  if (res.ok) applyAll(res.data)
}

/** Poll: updates only runtime status, never the form. Failures are ignored. */
export async function refreshCamera(): Promise<void> {
  if (!cameraState.loaded) return
  const res = await window.petConsole.backend<CameraStatus>('GET', '/api/camera')
  if (res.ok) {
    cameraState.offline = false
    applyRuntime(res.data)
  } else if (res.offline) {
    cameraState.offline = true
    cameraState.error = res.error
  }
}

/** Throws a user-facing message on failure. */
export async function saveCamera(): Promise<void> {
  const res = await window.petConsole.backend<CameraStatus>('PUT', '/api/camera', clone(cameraState.form))
  if (!res.ok) throw new Error(res.error)
  applyAll(res.data)
}

export async function cameraLook(): Promise<CameraLookResult> {
  const res = await window.petConsole.backend<CameraLookResult>('POST', '/api/camera/look')
  if (!res.ok) throw new Error(res.error)
  return res.data
}

export function formatGreetingTime(ts: number): string {
  const d = new Date(ts * 1000)
  const p = (n: number): string => String(n).padStart(2, '0')
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}
