import { computed, reactive } from 'vue'
import type {
  KeyStatus,
  LlmProfile,
  LlmSave,
  LlmState,
  LlmTestRequest,
  LlmTestResult
} from '../../shared/backend'

export const RESERVED_EXTRA = ['model', 'messages', 'stream', 'tools', 'tool_choice', 'functions', 'n']

export interface DraftProfile extends Omit<LlmProfile, 'extra'> {
  uid: number
  /** Name as stored in the backend; null for profiles not saved yet. */
  savedName: string | null
  key: KeyStatus
  /** Typed but not yet stored key; never read back from the backend. */
  newKey: string
  extraText: string
}

export type LlmRole = 'chat' | 'vision' | 'memory'
export const MAX_FALLBACKS = 2

interface Store {
  loading: boolean
  loaded: boolean
  offline: boolean
  error: string | null
  profiles: DraftProfile[]
  /** Role -> profile uid, so roles follow a profile through renames. */
  roles: { chat: number | null; vision: number | null; memory: number | null }
  /** Role -> [2nd place, 3rd place] profile uids (null = empty slot). */
  fallbacks: Record<LlmRole, (number | null)[]>
  /** Global switch for web search during chat. */
  webSearch: boolean
  snapshot: string
}

let nextUid = 1

function emptyFallbacks(): Record<LlmRole, (number | null)[]> {
  return { chat: [null, null], vision: [null, null], memory: [null, null] }
}

export const llm = reactive<Store>({
  loading: false,
  loaded: false,
  offline: false,
  error: null,
  profiles: [],
  roles: { chat: null, vision: null, memory: null },
  fallbacks: emptyFallbacks(),
  webSearch: false,
  snapshot: ''
})

export function parseExtra(text: string): { ok: true; value: Record<string, unknown> } | { ok: false; error: string } {
  if (!text.trim()) return { ok: true, value: {} }
  let v: unknown
  try {
    v = JSON.parse(text)
  } catch (e) {
    return { ok: false, error: `JSON 格式错误：${e instanceof Error ? e.message : String(e)}` }
  }
  if (typeof v !== 'object' || v === null || Array.isArray(v)) return { ok: false, error: '必须是 JSON 对象 {…}' }
  const bad = Object.keys(v).filter((k) => RESERVED_EXTRA.includes(k))
  if (bad.length) return { ok: false, error: `不能包含保留字段：${bad.join(', ')}` }
  return { ok: true, value: v as Record<string, unknown> }
}

/** Backup uids for a role with empty slots, duplicates and the primary removed, order kept. */
export function cleanFallbacks(role: LlmRole): number[] {
  const primary = llm.roles[role]
  if (primary === null) return []
  const out: number[] = []
  for (const uid of llm.fallbacks[role]) {
    if (uid !== null && uid !== primary && !out.includes(uid) && llm.profiles.some((p) => p.uid === uid)) out.push(uid)
  }
  return out.slice(0, MAX_FALLBACKS)
}

function fallbackNames(role: LlmRole): string[] {
  return cleanFallbacks(role).map((uid) => llm.profiles.find((p) => p.uid === uid)?.name.trim() ?? '')
}

function toDraft(p: LlmState['profiles'][number]): DraftProfile {
  const { key, extra, ...rest } = p
  return {
    ...rest,
    stream: p.stream ?? false,
    uid: nextUid++,
    savedName: p.name,
    key,
    newKey: '',
    extraText: Object.keys(extra).length ? JSON.stringify(extra, null, 2) : ''
  }
}

function serialize(): string {
  return JSON.stringify({
    profiles: llm.profiles.map(({ uid: _u, key: _k, newKey: _n, ...rest }) => rest),
    roles: llm.roles,
    fallbacks: { chat: cleanFallbacks('chat'), vision: cleanFallbacks('vision'), memory: cleanFallbacks('memory') },
    webSearch: llm.webSearch
  })
}

export const dirty = computed(
  () => llm.loaded && (serialize() !== llm.snapshot || llm.profiles.some((p) => p.newKey.trim() !== ''))
)

function apply(state: LlmState): void {
  llm.profiles = state.profiles.map(toDraft)
  const uidOf = (name: string | null): number | null => llm.profiles.find((p) => p.name === name)?.uid ?? null
  llm.roles = {
    chat: uidOf(state.roles.chat),
    vision: uidOf(state.roles.vision),
    memory: uidOf(state.roles.memory)
  }
  const slots = (names: string[]): (number | null)[] => [uidOf(names[0] ?? null), uidOf(names[1] ?? null)]
  llm.fallbacks = {
    chat: slots(state.fallbacks?.chat ?? []),
    vision: slots(state.fallbacks?.vision ?? []),
    memory: slots(state.fallbacks?.memory ?? [])
  }
  llm.webSearch = state.web_search
  llm.snapshot = serialize()
  llm.loaded = true
}

export async function loadLlm(): Promise<void> {
  llm.loading = true
  try {
    const res = await window.petConsole.backend<LlmState>('GET', '/api/llm')
    llm.offline = !res.ok && res.offline
    llm.error = res.ok ? null : res.error
    if (res.ok) apply(res.data)
  } finally {
    llm.loading = false
  }
}

export function profileOf(d: DraftProfile): LlmProfile {
  const extra = parseExtra(d.extraText)
  if (!extra.ok) throw new Error(`「${d.name}」自定义参数：${extra.error}`)
  return {
    name: d.name.trim(),
    base_url: d.base_url.trim(),
    model: d.model.trim(),
    max_tokens: d.max_tokens,
    temperature: d.temperature,
    top_p: d.top_p,
    presence_penalty: d.presence_penalty,
    frequency_penalty: d.frequency_penalty,
    timeout: d.timeout,
    stream: d.stream,
    web_search: d.web_search,
    extra: extra.value
  }
}

/** Saves the whole config, then any newly typed keys. Throws a user-facing message on failure. */
export async function saveLlm(): Promise<void> {
  const nameOf = (uid: number | null): string | null =>
    uid === null ? null : (llm.profiles.find((p) => p.uid === uid)?.name.trim() ?? null)
  const body: LlmSave = {
    profiles: llm.profiles.map((d) => ({ ...profileOf(d), renamed_from: d.savedName })),
    roles: {
      chat: nameOf(llm.roles.chat),
      vision: nameOf(llm.roles.vision),
      memory: nameOf(llm.roles.memory)
    },
    fallbacks: { chat: fallbackNames('chat'), vision: fallbackNames('vision'), memory: fallbackNames('memory') },
    web_search: llm.webSearch
  }
  const pendingKeys = llm.profiles
    .filter((d) => d.newKey.trim())
    .map((d) => ({ profile: d.name.trim(), api_key: d.newKey.trim() }))

  const res = await window.petConsole.backend<LlmState>('PUT', '/api/llm', body)
  if (!res.ok) throw new Error(res.error)
  for (const k of pendingKeys) {
    const r = await window.petConsole.backend<KeyStatus>('PUT', '/api/llm/key', k)
    if (!r.ok) {
      apply(res.data)
      throw new Error(`「${k.profile}」的 API Key 保存失败：${r.error}`)
    }
  }
  if (pendingKeys.length === 0) return apply(res.data)
  await loadLlm()
}

export async function clearKey(d: DraftProfile): Promise<void> {
  d.newKey = ''
  if (!d.savedName) return
  const res = await window.petConsole.backend<KeyStatus>('DELETE', '/api/llm/key', { profile: d.savedName })
  if (!res.ok) throw new Error(res.error)
  d.key = res.data
}

export async function testProfile(d: DraftProfile, kind: 'chat' | 'vision'): Promise<LlmTestResult> {
  const req: LlmTestRequest = { kind, profile: profileOf(d), key_profile: d.savedName }
  const res = await window.petConsole.backend<LlmTestResult>('POST', '/api/llm/test', req)
  if (!res.ok) throw new Error(res.error)
  return res.data
}

export function addProfile(): DraftProfile {
  const taken = new Set(llm.profiles.map((p) => p.name))
  let i = llm.profiles.length + 1
  while (taken.has(`配置${i}`)) i++
  const d: DraftProfile = {
    uid: nextUid++,
    savedName: null,
    name: `配置${i}`,
    base_url: 'https://api.openai.com/v1',
    model: 'gpt-4o-mini',
    max_tokens: 1024,
    temperature: 0.8,
    top_p: null,
    presence_penalty: null,
    frequency_penalty: null,
    timeout: 60,
    stream: false,
    web_search: 'off',
    key: { set: false, hint: null },
    newKey: '',
    extraText: ''
  }
  llm.profiles.push(d)
  if (llm.roles.chat === null) llm.roles.chat = d.uid
  return d
}

export function removeProfile(uid: number): void {
  llm.profiles = llm.profiles.filter((p) => p.uid !== uid)
  if (llm.roles.chat === uid) llm.roles.chat = null
  if (llm.roles.vision === uid) llm.roles.vision = null
  if (llm.roles.memory === uid) llm.roles.memory = null
  for (const list of Object.values(llm.fallbacks)) {
    list.forEach((u, i) => {
      if (u === uid) list[i] = null
    })
  }
}

export function revertLlm(): void {
  void loadLlm()
}
