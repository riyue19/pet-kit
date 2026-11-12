import { readFile } from 'node:fs/promises'
import { homedir } from 'node:os'
import { join } from 'node:path'
import { BACKEND_PATH_RE, type BackendMethod, type BackendResult } from '../shared/backend'

const REQUEST_TIMEOUT_MS = 10 * 60 * 1000
const METHODS: readonly BackendMethod[] = ['GET', 'PUT', 'POST', 'DELETE']

export interface Endpoint {
  port: number
  token: string
}

export function endpointFile(): string {
  const base = process.env['LOCALAPPDATA'] ?? join(homedir(), '.local', 'share')
  return join(base, 'pet-kit', 'backend.json')
}

/** Re-read every call: the backend writes a fresh token each time it starts. */
export async function readEndpoint(): Promise<Endpoint | null> {
  try {
    const v = JSON.parse(await readFile(endpointFile(), 'utf8')) as Partial<Endpoint>
    if (typeof v.port !== 'number' || typeof v.token !== 'string') return null
    return { port: v.port, token: v.token }
  } catch {
    return null
  }
}

/** Parks an image at the backend (POST /api/pet/image); returns its id or a readable error. */
export async function uploadImage(dataUrl: string): Promise<{ ok: true; id: string } | { ok: false; error: string }> {
  const res = await rawRequest('POST', '/api/pet/image', { data_url: dataUrl })
  if (!res.ok) return { ok: false, error: res.error }
  const id = (res.data as { id?: unknown } | null)?.id
  return typeof id === 'string' ? { ok: true, id } : { ok: false, error: '后端返回了意外的内容' }
}

const OFFLINE = '后端未运行（在 backend 目录执行 uv run python -m api）'

export async function backendRequest(method: unknown, path: unknown, body?: unknown): Promise<BackendResult> {
  if (typeof method !== 'string' || !METHODS.includes(method as BackendMethod)) {
    return { ok: false, offline: false, status: null, error: `不允许的方法：${String(method)}` }
  }
  if (typeof path !== 'string' || !BACKEND_PATH_RE.test(path)) {
    return { ok: false, offline: false, status: null, error: `不允许的路径：${String(path)}` }
  }
  return rawRequest(method as BackendMethod, path, body)
}

/** No path whitelist: only for fixed paths used by the main process itself, never for renderer-supplied ones. */
async function rawRequest(method: BackendMethod, path: string, body?: unknown): Promise<BackendResult> {
  const ep = await readEndpoint()
  if (!ep) return { ok: false, offline: true, status: null, error: OFFLINE }

  let res: Response
  try {
    res = await fetch(`http://127.0.0.1:${ep.port}${path}`, {
      method,
      headers: { Authorization: `Bearer ${ep.token}`, 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS)
    })
  } catch {
    return { ok: false, offline: true, status: null, error: OFFLINE }
  }

  let text: string
  try {
    text = await res.text()
  } catch {
    return { ok: false, offline: true, status: null, error: OFFLINE }
  }
  let data: unknown = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = text
  }
  if (res.ok) return { ok: true, data }
  if (res.status === 401) {
    return { ok: false, offline: true, status: 401, error: '后端令牌不匹配，请重启后端' }
  }
  const detail = (data as { detail?: unknown } | null)?.detail
  const error = typeof detail === 'string' ? detail : `HTTP ${res.status}`
  return { ok: false, offline: false, status: res.status, error }
}
