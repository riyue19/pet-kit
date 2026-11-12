import { computed, reactive } from 'vue'
import { clone } from './util'
import type {
  AsrConfig,
  HeardEntry,
  KeyStatus,
  TtsConfig,
  TtsProviderName,
  VoiceInfo,
  VoiceSave,
  VoiceState,
  VoiceTestRequest,
  VoiceTestResult
} from '../../shared/backend'

interface Store {
  loading: boolean
  loaded: boolean
  offline: boolean
  error: string | null
  asr: AsrConfig
  tts: TtsConfig
  modelsReady: boolean
  fetchCommand: string
  /** Key status of each provider. */
  keys: Record<TtsProviderName, KeyStatus>
  /** Typed but not yet stored key; never read back from the backend. */
  newKey: string
  emotionChoices: string[]
  modelChoices: string[]
  cosyModelChoices: string[]
  /** JSON of the last saved/loaded VoiceSave. */
  snapshot: string
  voices: VoiceInfo[]
  voicesLoading: boolean
  voicesError: string | null
  heard: HeardEntry[]
  heardError: string | null
}

const emptyAsr = (): AsrConfig => ({
  listen: false,
  language: 'zh',
  min_chars: 2,
  short_min_seconds: 1,
  model_dir: 'models/asr',
  threads: 2,
  vad_threshold: 0.5,
  min_silence_seconds: 0.6,
  min_speech_seconds: 0.25,
  max_speech_seconds: 20,
  short_chars: 5,
  drop_fillers: true,
  ignore_phrases: [],
  barge_in: false,
  barge_in_seconds: 0.4
})

const emptyTts = (): TtsConfig => ({
  enabled: true,
  provider: 'minimax',
  speech_language: 'same',
  minimax: { base_url: 'https://api.minimax.cn', model: 'speech-2.8-turbo', voice_id: 'female-shaonv', speed: 1, vol: 1, pitch: 0, timeout: 30 },
  cosyvoice: {
    url: 'wss://dashscope.aliyuncs.com/api-ws/v1/inference',
    model: 'cosyvoice-v3-flash',
    voice: 'longanhuan',
    volume: 50,
    rate: 1,
    pitch: 1,
    emotion_instruct: false,
    instruction: '',
    timeout: 30
  },
  openai: { base_url: 'https://api.openai.com/v1', model: 'gpt-4o-mini-tts', voice: 'alloy', speed: 1, response_format: 'pcm', sample_rate: 24000, extra: {}, timeout: 30 },
  emotions: {}
})

export const voice = reactive<Store>({
  loading: false,
  loaded: false,
  offline: false,
  error: null,
  asr: emptyAsr(),
  tts: emptyTts(),
  modelsReady: true,
  fetchCommand: '',
  keys: { minimax: { set: false, hint: null }, cosyvoice: { set: false, hint: null }, openai: { set: false, hint: null } },
  newKey: '',
  emotionChoices: [],
  modelChoices: [],
  cosyModelChoices: [],
  snapshot: '',
  voices: [],
  voicesLoading: false,
  voicesError: null,
  heard: [],
  heardError: null
})

export async function loadHeard(): Promise<void> {
  try {
    const res = await window.petConsole.backend<HeardEntry[]>('GET', '/api/voice/heard')
    if (res.ok) {
      voice.heard = res.data
      voice.heardError = null
    } else {
      voice.heardError = res.error
    }
  } catch (e) {
    voice.heardError = e instanceof Error ? e.message : String(e)
  }
}


function serialize(): string {
  return JSON.stringify({ asr: voice.asr, tts: voice.tts })
}

export const dirty = computed(() => voice.loaded && serialize() !== voice.snapshot)

function apply(state: VoiceState): void {
  voice.asr = clone(state.asr)
  // Defaults underneath: a backend started before an update (the launcher reuses a running one) may not
  // send the newer providers, keys or model lists yet, and the page must still render.
  voice.tts = { ...emptyTts(), ...clone(state.tts) }
  voice.modelsReady = state.models_ready
  voice.fetchCommand = state.fetch_command
  const none: KeyStatus = { set: false, hint: null }
  const given: Partial<Record<TtsProviderName, KeyStatus>> = state.keys ?? {}
  // An older backend only reports the key of the saved provider.
  if (!state.keys && state.key) given[voice.tts.provider] = state.key
  voice.keys = { minimax: given.minimax ?? none, cosyvoice: given.cosyvoice ?? none, openai: given.openai ?? none }
  voice.emotionChoices = state.emotions ?? []
  voice.modelChoices = state.models ?? []
  voice.cosyModelChoices = state.cosyvoice_models ?? []
  voice.snapshot = serialize()
  voice.loaded = true
}

export async function loadVoice(): Promise<void> {
  voice.loading = true
  try {
    const res = await window.petConsole.backend<VoiceState>('GET', '/api/voice')
    voice.offline = !res.ok && res.offline
    voice.error = res.ok ? null : res.error
    if (res.ok) {
      apply(res.data)
      if (voice.keys.minimax.set) void loadVoices()
    }
  } finally {
    voice.loading = false
  }
}

/** Re-fetches only the "models ready" flag etc. without discarding unsaved edits. */
export async function recheckModels(): Promise<void> {
  const res = await window.petConsole.backend<VoiceState>('GET', '/api/voice')
  voice.offline = !res.ok && res.offline
  if (!res.ok) throw new Error(res.error)
  voice.modelsReady = res.data.models_ready
  voice.fetchCommand = res.data.fetch_command
}

export function revertVoice(): void {
  void loadVoice()
}

/** Saves the whole form. Throws a user-facing message on failure. */
export async function saveVoice(): Promise<void> {
  const body: VoiceSave = { asr: clone(voice.asr), tts: clone(voice.tts) }
  const res = await window.petConsole.backend<VoiceState>('PUT', '/api/voice', body)
  if (!res.ok) throw new Error(res.error)
  apply(res.data)
}

/**
 * Saves only the listen flag on top of the last saved config, so other unsaved edits are not sent.
 * Throws on failure (caller restores the switch).
 */
export async function saveListen(listen: boolean): Promise<void> {
  const saved = JSON.parse(voice.snapshot) as VoiceSave
  saved.asr.listen = listen
  const res = await window.petConsole.backend<VoiceState>('PUT', '/api/voice', saved)
  if (!res.ok) throw new Error(res.error)
  voice.snapshot = JSON.stringify(saved)
  voice.asr.listen = listen
}

export async function loadVoices(): Promise<void> {
  voice.voicesLoading = true
  try {
    const res = await window.petConsole.backend<VoiceInfo[]>('GET', '/api/voice/voices')
    if (res.ok) {
      voice.voices = res.data
      voice.voicesError = null
    } else {
      voice.voicesError = res.error
    }
  } finally {
    voice.voicesLoading = false
  }
}

export async function saveKey(): Promise<void> {
  const key = voice.newKey.trim()
  if (!key) return
  const provider = voice.tts.provider
  const res = await window.petConsole.backend<KeyStatus>('PUT', '/api/voice/key', { api_key: key, provider })
  if (!res.ok) throw new Error(res.error)
  voice.newKey = ''
  voice.keys[provider] = res.data
  if (provider === 'minimax') void loadVoices()
}

export async function deleteKey(): Promise<void> {
  voice.newKey = ''
  const provider = voice.tts.provider
  const res = await window.petConsole.backend<KeyStatus>('DELETE', `/api/voice/key?provider=${provider}`)
  if (!res.ok) throw new Error(res.error)
  voice.keys[provider] = res.data
  if (provider === 'minimax') {
    voice.voices = []
    voice.voicesError = null
  }
}

export async function testVoice(text: string, expression: string | null): Promise<VoiceTestResult> {
  const req: VoiceTestRequest = { text: text.trim() ? text : null, tts: clone(voice.tts), expression }
  const res = await window.petConsole.backend<VoiceTestResult>('POST', '/api/voice/test', req)
  if (!res.ok) throw new Error(res.error)
  return res.data
}

const KIND_LABELS: Record<VoiceInfo['kind'], string> = {
  voice_cloning: '复刻音色',
  voice_generation: '文生音色',
  system_voice: '系统音色'
}

export interface VoiceOptionGroup {
  type: 'group'
  label: string
  key: string
  children: { label: string; value: string }[]
}

/** Groups voices by kind, in a fixed order, skipping empty groups. */
export function groupVoices(voices: VoiceInfo[]): VoiceOptionGroup[] {
  return (Object.keys(KIND_LABELS) as VoiceInfo['kind'][])
    .map((kind) => ({
      type: 'group' as const,
      label: KIND_LABELS[kind],
      key: kind,
      children: voices
        .filter((v) => v.kind === kind)
        .map((v) => ({ label: `${v.name}（${v.voice_id}）`, value: v.voice_id }))
    }))
    .filter((g) => g.children.length > 0)
}

/** Returns a new emotions map with `label` set, or removed when `emotion` is null. Other keys are kept. */
export function withEmotion(emotions: Record<string, string>, label: string, emotion: string | null): Record<string, string> {
  const next = { ...emotions }
  if (emotion === null) delete next[label]
  else next[label] = emotion
  return next
}
