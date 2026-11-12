import type { AgentOutput, LinkStatus, StatusChange, VoiceMessage } from '../shared/agent'
import type { CodePayload } from '../shared/ipc'
import { isSafeAiCss } from '../shared/ui-style'
import { FISHING_OFF, toFishingMessage, type FishingMessage } from '../shared/fishing'
import { readEndpoint, type Endpoint } from './backend'

const RETRY_MIN_MS = 2000
const RETRY_MAX_MS = 15000

export interface ConfirmRequest {
  id: string
  title: string
  detail: string
}

export interface Labels {
  expressions: string[]
  motions: string[]
  /** Note names of the enabled stickers. */
  stickers?: string[]
}

/** The subset of the WHATWG WebSocket the link uses (injectable for tests). */
export interface SocketLike {
  readyState: number
  binaryType?: string
  send(data: string | ArrayBuffer | Uint8Array): void
  close(): void
  onopen: (() => void) | null
  onmessage: ((e: { data: unknown }) => void) | null
  onclose: ((e: { code: number }) => void) | null
  onerror: (() => void) | null
}

export interface PetLinkDeps {
  labels(): Labels
  onOutput(output: AgentOutput): void
  onStatus(status: LinkStatus): void
  /** Voice control messages; a {type:'mic', listen:false} is also sent whenever the link goes offline. */
  onVoice?(m: VoiceMessage): void
  /** A skill wants the user's OK; answer with PetLink.sendConfirm (no answer = the backend times out = no). */
  onConfirm?(req: ConfirmRequest): void
  /** The model asked to show a piece of code (show_code). */
  onCode?(code: CodePayload): void
  /** The model proposed CSS for its own look (set_style); the user decides whether to keep it. */
  onSetCss?(p: { css: string; replace: boolean }): void
  /** The pet proposed a mini game (invite_game); the user decides whether to open the 小游戏 page. */
  onGameInvite?(p: { game: string; name: string; pet: string }): void
  /** Fishing HUD state / timelines; FISHING_OFF is also sent whenever the link goes offline. */
  onFishing?(m: FishingMessage): void
  /** TTS PCM chunk (binary frame). */
  onAudio?(chunk: Uint8Array): void
  readEndpoint?: () => Promise<Endpoint | null>
  openSocket?: (url: string) => SocketLike
}

const OPEN = 1

/** Validates a backend voice message; unknown or malformed messages yield null. */
export function toVoiceMessage(msg: Record<string, unknown>): VoiceMessage | null {
  const id = typeof msg.id === 'number' && Number.isInteger(msg.id) ? msg.id : null
  switch (msg.type) {
    case 'mic':
      return { type: 'mic', listen: msg.listen === true }
    case 'camera':
      return {
        type: 'camera',
        on: msg.on === true,
        present: typeof msg.present === 'boolean' ? msg.present : null,
        enabled: msg.enabled === true
      }
    case 'hearing':
      return { type: 'hearing', active: msg.active === true }
    case 'heard':
      return typeof msg.text === 'string' ? { type: 'heard', text: msg.text.slice(0, 500) } : null
    case 'interrupt':
      return { type: 'interrupt' }
    case 'audio_start': {
      const sr = msg.sample_rate
      if (id === null || typeof sr !== 'number' || !(sr >= 8000 && sr <= 48000)) return null
      return { type: 'audio_start', id, sample_rate: sr }
    }
    case 'audio_end':
      if (id === null) return null
      return typeof msg.error === 'string' ? { type: 'audio_end', id, error: msg.error } : { type: 'audio_end', id }
    default:
      return null
  }
}

function toBytes(data: unknown): Uint8Array | null {
  if (data instanceof ArrayBuffer) return new Uint8Array(data)
  if (ArrayBuffer.isView(data)) return new Uint8Array(data.buffer, data.byteOffset, data.byteLength)
  return null
}

const CODE_MAX_CHARS = 20000

/** Validates a `code` message from the backend; anything odd is dropped. */
export function toCode(msg: Record<string, unknown>): CodePayload | null {
  const { title, language, code } = msg
  if (typeof code !== 'string' || !code.trim() || code.length > CODE_MAX_CHARS) return null
  return {
    title: typeof title === 'string' && title.trim() ? title.trim().slice(0, 60) : '代码',
    language: typeof language === 'string' && /^[A-Za-z0-9+#._-]{1,20}$/.test(language) ? language : '',
    code
  }
}

export function toStatus(v: unknown): StatusChange[] {
  if (!Array.isArray(v)) return []
  const out: StatusChange[] = []
  for (const x of v.slice(0, 6)) {
    if (typeof x !== 'object' || x === null) continue
    const c = x as Record<string, unknown>
    if (typeof c.id !== 'string' || typeof c.delta !== 'number' || !Number.isFinite(c.delta) || c.delta === 0) continue
    const num = (n: unknown): number => (typeof n === 'number' && Number.isFinite(n) ? n : 0)
    out.push({
      id: c.id.slice(0, 24),
      name: typeof c.name === 'string' && c.name ? c.name.slice(0, 12) : c.id.slice(0, 12),
      delta: c.delta,
      value: num(c.value),
      ratio: Math.min(1, Math.max(0, num(c.ratio))),
      reason: typeof c.reason === 'string' ? c.reason.slice(0, 20) : '',
      color: typeof c.color === 'string' && /^#[0-9a-fA-F]{6}$/.test(c.color) ? c.color : '#ff7eb6',
      tier: typeof c.tier === 'string' && c.tier ? c.tier.slice(0, 12) : null
    })
  }
  return out
}

function toOutput(v: unknown): AgentOutput | null {
  if (typeof v !== 'object' || v === null) return null
  const o = v as Record<string, unknown>
  const str = (x: unknown): string | null => (typeof x === 'string' && x ? x : null)
  const status = toStatus(o.status)
  return {
    text: typeof o.text === 'string' ? o.text : '',
    expression: str(o.expression),
    motion: str(o.motion),
    ...(typeof o.sticker === 'string' && o.sticker ? { sticker: o.sticker.slice(0, 64) } : {}),
    tool_calls: [],
    ...(status.length ? { status } : {})
  }
}

/**
 * Main-process WebSocket to the backend's /ws/pet. Holds the auth token (never exposed to renderers)
 * and reconnects with backoff, re-reading the endpoint file since each backend run has a new token.
 */
export class PetLink {
  private socket: SocketLike | null = null
  private retryTimer: NodeJS.Timeout | null = null
  private retryMs = RETRY_MIN_MS
  private stopped = true
  private status: LinkStatus = { state: 'offline', thinking: false }

  constructor(private readonly deps: PetLinkDeps) {}

  get current(): LinkStatus {
    return this.status
  }

  start(): void {
    if (!this.stopped) return
    this.stopped = false
    void this.connect()
  }

  stop(): void {
    this.stopped = true
    if (this.retryTimer) clearTimeout(this.retryTimer)
    this.retryTimer = null
    this.socket?.close()
    this.socket = null
    this.setStatus({ state: 'offline', thinking: false })
    this.deps.onVoice?.({ type: 'camera', on: false, present: null, enabled: false })
    this.deps.onVoice?.({ type: 'mic', listen: false })
    this.deps.onFishing?.(FISHING_OFF)
  }

  /** Returns false if the backend is not connected. */
  sendEvent(type: 'user.text' | 'pet.interaction' | 'hotkey.pressed', payload: Record<string, unknown>): boolean {
    return this.send({ type: 'event', event: { type, payload } })
  }

  /** Microphone PCM frame; dropped while offline. */
  sendAudio(frame: Uint8Array): boolean {
    if (this.status.state !== 'online' || !this.socket || this.socket.readyState !== OPEN) return false
    this.socket.send(frame)
    return true
  }

  sendPlayback(playing: boolean): boolean {
    return this.send({ type: 'playback', playing })
  }

  /** Stop speaking now and drop queued replies (stop_speaking hotkey). */
  sendStop(): boolean {
    return this.send({ type: 'stop' })
  }

  /** The push_to_talk hotkey went down; the backend watches that declared combo for the release. */
  sendPtt(id: string): boolean {
    return this.send({ type: 'ptt', id })
  }

  sendConfirm(id: string, allow: boolean): boolean {
    return this.send({ type: 'confirm_response', id, allow })
  }

  /** Answer to a bite window of cast `seq`. */
  sendFishingHook(seq: number, clicked: boolean): boolean {
    return this.send({ type: 'fishing.hook', seq, clicked })
  }

  /** The pet menu's fishing switch; anything that spends or sells goes through the console or a typed command. */
  sendFishingCmd(cmd: 'start' | 'stop'): boolean {
    return this.send({ type: 'fishing.cmd', cmd })
  }

  updateLabels(): void {
    this.send({ type: 'labels', labels: this.deps.labels() })
  }

  private send(msg: unknown): boolean {
    if (this.status.state !== 'online' || !this.socket || this.socket.readyState !== OPEN) return false
    this.socket.send(JSON.stringify(msg))
    return true
  }

  private setStatus(next: LinkStatus): void {
    if (next.state === this.status.state && next.thinking === this.status.thinking) return
    this.status = next
    this.deps.onStatus(next)
  }

  private scheduleRetry(): void {
    if (this.stopped || this.retryTimer) return
    this.retryTimer = setTimeout(() => {
      this.retryTimer = null
      void this.connect()
    }, this.retryMs)
    this.retryMs = Math.min(this.retryMs * 2, RETRY_MAX_MS)
  }

  private async connect(): Promise<void> {
    if (this.stopped) return
    const ep = await (this.deps.readEndpoint ?? readEndpoint)()
    if (this.stopped) return
    if (!ep) return this.scheduleRetry()

    const open = this.deps.openSocket ?? ((url: string) => new WebSocket(url) as unknown as SocketLike)
    let socket: SocketLike
    try {
      socket = open(`ws://127.0.0.1:${ep.port}/ws/pet`)
    } catch (e) {
      console.warn('[pet-link] 无法创建 WebSocket：', e)
      return this.scheduleRetry()
    }
    this.socket = socket
    socket.binaryType = 'arraybuffer'
    socket.onopen = () => {
      socket.send(JSON.stringify({ type: 'hello', token: ep.token, labels: this.deps.labels() }))
    }
    socket.onmessage = (e) => {
      if (typeof e.data !== 'string') {
        const bytes = toBytes(e.data)
        if (bytes && this.status.state === 'online') this.deps.onAudio?.(bytes)
        return
      }
      let msg: Record<string, unknown>
      try {
        msg = JSON.parse(e.data) as Record<string, unknown>
      } catch {
        console.warn('[pet-link] 后端发来无法解析的消息：', e.data.slice(0, 200))
        return
      }
      if (msg.type === 'ready') {
        this.retryMs = RETRY_MIN_MS
        this.setStatus({ state: 'online', thinking: false })
      } else if (msg.type === 'status') {
        this.setStatus({ state: 'online', thinking: msg.thinking === true })
      } else if (msg.type === 'output') {
        const out = toOutput(msg.output)
        if (out) this.deps.onOutput(out)
      } else if (msg.type === 'confirm_request') {
        if (typeof msg.id === 'string' && typeof msg.title === 'string' && typeof msg.detail === 'string') {
          this.deps.onConfirm?.({ id: msg.id, title: msg.title.slice(0, 100), detail: msg.detail.slice(0, 300) })
        }
      } else if (msg.type === 'code') {
        const code = toCode(msg)
        if (code) this.deps.onCode?.(code)
      } else if (msg.type === 'set_css') {
        if (isSafeAiCss(msg.css)) this.deps.onSetCss?.({ css: msg.css.trim(), replace: msg.replace === true })
      } else if (msg.type === 'game_invite') {
        if (typeof msg.game === 'string' && /^[a-z]{1,20}$/.test(msg.game) && typeof msg.name === 'string') {
          const pet = typeof msg.pet === 'string' && msg.pet ? msg.pet.slice(0, 32) : '桌宠'
          this.deps.onGameInvite?.({ game: msg.game, name: msg.name.slice(0, 20), pet })
        }
      } else if (msg.type === 'fishing.state' || msg.type === 'fishing.anim') {
        const fishing = toFishingMessage(msg)
        if (fishing) this.deps.onFishing?.(fishing)
      } else if (msg.type === 'error' && typeof msg.message === 'string') {
        console.warn('[pet-link] backend rejected message:', msg.message)
      } else {
        const voice = toVoiceMessage(msg)
        if (voice) this.deps.onVoice?.(voice)
      }
    }
    socket.onerror = () => undefined
    socket.onclose = (ev) => {
      if (this.socket !== socket) return
      this.socket = null
      if (this.status.state === 'online') console.warn(`[pet-link] 与后端的连接断开（code ${ev.code}）`)
      this.setStatus({ state: 'offline', thinking: false })
      this.deps.onVoice?.({ type: 'camera', on: false, present: null, enabled: false })
      this.deps.onVoice?.({ type: 'mic', listen: false })
      this.deps.onFishing?.(FISHING_OFF)
      this.scheduleRetry()
    }
  }
}
