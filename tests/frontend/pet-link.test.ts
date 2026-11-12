import { afterEach, describe, expect, it, vi } from 'vitest'
import { PetLink, toStatus, toVoiceMessage, type ConfirmRequest, type SocketLike } from '../../frontend/src/main/pet-link'
import type { AgentOutput, LinkStatus, VoiceMessage } from '../../frontend/src/shared/agent'

class FakeSocket implements SocketLike {
  readyState = 0
  binaryType = 'blob'
  sent: unknown[] = []
  onopen: (() => void) | null = null
  onmessage: ((e: { data: unknown }) => void) | null = null
  onclose: ((e: { code: number }) => void) | null = null
  onerror: (() => void) | null = null
  constructor(readonly url: string) {}
  send(data: string | ArrayBuffer | Uint8Array): void {
    this.sent.push(typeof data === 'string' ? JSON.parse(data) : data)
  }
  close(): void {
    this.readyState = 3
    this.onclose?.({ code: 1000 })
  }
  open(): void {
    this.readyState = 1
    this.onopen?.()
  }
  receive(msg: unknown): void {
    this.onmessage?.({ data: typeof msg === 'string' ? msg : JSON.stringify(msg) })
  }
  receiveBinary(data: ArrayBuffer): void {
    this.onmessage?.({ data })
  }
  drop(code = 1006): void {
    this.readyState = 3
    this.onclose?.({ code })
  }
}

function setup(endpoint: { port: number; token: string } | null = { port: 1234, token: 'tok' }) {
  const sockets: FakeSocket[] = []
  const outputs: AgentOutput[] = []
  const statuses: LinkStatus[] = []
  const voices: VoiceMessage[] = []
  const audio: Uint8Array[] = []
  const confirms: ConfirmRequest[] = []
  const codes: unknown[] = []
  const cssProposals: unknown[] = []
  const invites: unknown[] = []
  const readEndpoint = vi.fn(async () => endpoint)
  const link = new PetLink({
    labels: () => ({ expressions: ['happy'], motions: ['wave'] }),
    onOutput: (o) => outputs.push(o),
    onStatus: (s) => statuses.push(s),
    onVoice: (m) => voices.push(m),
    onAudio: (c) => audio.push(c),
    onConfirm: (r) => confirms.push(r),
    onCode: (c) => codes.push(c),
    onSetCss: (p) => cssProposals.push(p),
    onGameInvite: (p) => invites.push(p),
    readEndpoint,
    openSocket: (url) => {
      const s = new FakeSocket(url)
      sockets.push(s)
      return s
    }
  })
  return { link, sockets, outputs, statuses, readEndpoint, voices, audio, confirms, codes, cssProposals, invites }
}

const flush = (): Promise<void> => new Promise((r) => setTimeout(r, 0))

describe('PetLink', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('relays confirm requests and sends the answer back; ignores malformed ones', async () => {
    const { link, sockets, confirms } = setup()
    link.start()
    await flush()
    sockets[0].open()
    sockets[0].receive({ type: 'ready' })
    sockets[0].receive({ type: 'confirm_request', id: 'c1', title: 't', detail: '启动游戏Steam' })
    sockets[0].receive({ type: 'confirm_request', id: 5, title: 't', detail: 'd' })
    expect(confirms).toEqual([{ id: 'c1', title: 't', detail: '启动游戏Steam' }])
    expect(link.sendConfirm('c1', true)).toBe(true)
    expect(sockets[0].sent.at(-1)).toEqual({ type: 'confirm_response', id: 'c1', allow: true })
  })

  it('relays code and set_css messages, dropping malformed or unsafe ones', async () => {
    const { link, sockets, codes, cssProposals } = setup()
    link.start()
    await flush()
    sockets[0].open()
    sockets[0].receive({ type: 'ready' })
    sockets[0].receive({ type: 'code', title: '  问候  ', language: 'python', code: 'print(1)' })
    sockets[0].receive({ type: 'code', title: 5, language: 'not a lang!', code: 'x' })
    sockets[0].receive({ type: 'code', title: 't', code: '   ' })
    sockets[0].receive({ type: 'code', title: 't', code: 'x'.repeat(20001) })
    expect(codes).toEqual([
      { title: '问候', language: 'python', code: 'print(1)' },
      { title: '代码', language: '', code: 'x' }
    ])
    sockets[0].receive({ type: 'set_css', css: '  #bubble { --b-bg: red; }  ' })
    sockets[0].receive({ type: 'set_css', css: '', replace: true })
    sockets[0].receive({ type: 'set_css', css: '@import url(http://evil/x.css);' })
    sockets[0].receive({ type: 'set_css', css: 5 })
    expect(cssProposals).toEqual([
      { css: '#bubble { --b-bg: red; }', replace: false },
      { css: '', replace: true }
    ])
  })

  it('relays game invitations, dropping malformed ones', async () => {
    const { link, sockets, invites } = setup()
    link.start()
    await flush()
    sockets[0].open()
    sockets[0].receive({ type: 'ready' })
    sockets[0].receive({ type: 'game_invite', game: 'dice', name: '吹牛骰', pet: '小白' })
    sockets[0].receive({ type: 'game_invite', game: 'rps', name: '骗子猜拳' })
    sockets[0].receive({ type: 'game_invite', game: '../x', name: 'x' })
    sockets[0].receive({ type: 'game_invite', game: 'dice' })
    expect(invites).toEqual([
      { game: 'dice', name: '吹牛骰', pet: '小白' },
      { game: 'rps', name: '骗子猜拳', pet: '桌宠' }
    ])
  })

  it('sends hello with token and labels, goes online on ready', async () => {
    const { link, sockets, statuses } = setup()
    link.start()
    await flush()
    expect(sockets[0].url).toBe('ws://127.0.0.1:1234/ws/pet')
    sockets[0].open()
    expect(sockets[0].sent[0]).toEqual({ type: 'hello', token: 'tok', labels: { expressions: ['happy'], motions: ['wave'] } })
    expect(link.current.state).toBe('offline')
    sockets[0].receive({ type: 'ready' })
    expect(link.current).toEqual({ state: 'online', thinking: false })
    expect(statuses).toEqual([{ state: 'online', thinking: false }])
    link.stop()
  })

  it('refuses to send before ready', async () => {
    const { link, sockets } = setup()
    link.start()
    await flush()
    sockets[0].open()
    expect(link.sendEvent('user.text', { text: 'hi' })).toBe(false)
    sockets[0].receive({ type: 'ready' })
    expect(link.sendEvent('user.text', { text: 'hi' })).toBe(true)
    expect(sockets[0].sent[1]).toEqual({ type: 'event', event: { type: 'user.text', payload: { text: 'hi' } } })
    link.updateLabels()
    expect(sockets[0].sent[2]).toEqual({ type: 'labels', labels: { expressions: ['happy'], motions: ['wave'] } })
    link.stop()
  })

  it('tracks thinking and sanitizes output', async () => {
    const { link, sockets, outputs } = setup()
    link.start()
    await flush()
    sockets[0].open()
    sockets[0].receive({ type: 'ready' })
    sockets[0].receive({ type: 'status', thinking: true })
    expect(link.current.thinking).toBe(true)
    sockets[0].receive('not json')
    sockets[0].receive({
      type: 'output',
      output: { text: 'hello', expression: 'happy', motion: '', tool_calls: [{ name: 'rm' }] }
    })
    expect(outputs).toEqual([{ text: 'hello', expression: 'happy', motion: null, tool_calls: [] }])
    link.stop()
  })

  it('reconnects with backoff after a drop, re-reading the endpoint', async () => {
    vi.useFakeTimers()
    const { link, sockets, readEndpoint } = setup()
    link.start()
    await vi.advanceTimersByTimeAsync(0)
    sockets[0].open()
    sockets[0].receive({ type: 'ready' })
    sockets[0].drop()
    expect(link.current.state).toBe('offline')
    await vi.advanceTimersByTimeAsync(1999)
    expect(sockets).toHaveLength(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(sockets).toHaveLength(2)
    expect(readEndpoint).toHaveBeenCalledTimes(2)
    link.stop()
  })

  it('retries when no endpoint file exists and stops cleanly', async () => {
    vi.useFakeTimers()
    const { link, sockets, readEndpoint } = setup(null)
    link.start()
    await vi.advanceTimersByTimeAsync(0)
    expect(sockets).toHaveLength(0)
    await vi.advanceTimersByTimeAsync(2000)
    expect(readEndpoint).toHaveBeenCalledTimes(2)
    link.stop()
    await vi.advanceTimersByTimeAsync(60000)
    expect(readEndpoint).toHaveBeenCalledTimes(2)
  })

  it('relays voice messages and binary audio, and sends mic frames', async () => {
    const { link, sockets, voices, audio } = setup()
    link.start()
    await flush()
    const sock = sockets[0]
    expect(sock.binaryType).toBe('arraybuffer')
    sock.open()
    expect(link.sendAudio(new Uint8Array([1, 2]))).toBe(false)
    sock.receiveBinary(new Uint8Array([9, 9]).buffer)
    expect(audio).toEqual([])
    sock.receive({ type: 'ready' })
    sock.receive({ type: 'mic', listen: true })
    sock.receive({ type: 'audio_start', id: 3, sample_rate: 24000 })
    sock.receiveBinary(new Uint8Array([1, 2, 3, 4]).buffer)
    sock.receive({ type: 'audio_end', id: 3 })
    sock.receive({ type: 'interrupt' })
    expect(voices).toEqual([
      { type: 'mic', listen: true },
      { type: 'audio_start', id: 3, sample_rate: 24000 },
      { type: 'audio_end', id: 3 },
      { type: 'interrupt' }
    ])
    expect(Array.from(audio[0])).toEqual([1, 2, 3, 4])
    const frame = new Uint8Array([5, 6])
    expect(link.sendAudio(frame)).toBe(true)
    expect(link.sendPlayback(true)).toBe(true)
    expect(sock.sent.slice(-2)).toEqual([frame, { type: 'playback', playing: true }])
    link.stop()
  })

  it('turns the mic off whenever the link drops', async () => {
    vi.useFakeTimers()
    const { link, sockets, voices } = setup()
    link.start()
    await vi.advanceTimersByTimeAsync(0)
    sockets[0].open()
    sockets[0].receive({ type: 'ready' })
    sockets[0].receive({ type: 'mic', listen: true })
    sockets[0].drop()
    expect(voices.at(-1)).toEqual({ type: 'mic', listen: false })
    link.stop()
  })
})

describe('toVoiceMessage', () => {
  it('validates fields', () => {
    expect(toVoiceMessage({ type: 'mic', listen: 'yes' })).toEqual({ type: 'mic', listen: false })
    expect(toVoiceMessage({ type: 'camera', on: true })).toEqual({ type: 'camera', on: true, present: null, enabled: false })
    expect(toVoiceMessage({ type: 'camera', on: 'yes' })).toEqual({ type: 'camera', on: false, present: null, enabled: false })
    expect(toVoiceMessage({ type: 'heard', text: 3 })).toBeNull()
    expect(toVoiceMessage({ type: 'heard', text: 'x'.repeat(900) })).toEqual({ type: 'heard', text: 'x'.repeat(500) })
    expect(toVoiceMessage({ type: 'audio_start', id: 1, sample_rate: 1e6 })).toBeNull()
    expect(toVoiceMessage({ type: 'audio_start', id: 1.5, sample_rate: 24000 })).toBeNull()
    expect(toVoiceMessage({ type: 'audio_end', id: 2, error: 'boom' })).toEqual({ type: 'audio_end', id: 2, error: 'boom' })
    expect(toVoiceMessage({ type: 'whatever' })).toBeNull()
  })
})

describe('toStatus', () => {
  it('keeps well-formed changes and sanitizes the rest', () => {
    const ok = { id: 'affection', name: '好感度', delta: 0.3, value: 12.3, ratio: 0.27, reason: '被夸了', color: '#ff7eb6', tier: null }
    expect(toStatus([ok])).toEqual([ok])
    expect(toStatus('x')).toEqual([])
    expect(toStatus([null, { id: 'a', delta: 0 }, { id: 'a', delta: '1' }, { delta: 1 }])).toEqual([])
    const odd = toStatus([{ id: 'mood', delta: -1, ratio: 7, color: 'red; x', reason: 'r'.repeat(50), tier: 3 }])[0]
    expect(odd).toMatchObject({ id: 'mood', name: 'mood', delta: -1, value: 0, ratio: 1, color: '#ff7eb6', tier: null })
    expect(odd.reason).toHaveLength(20)
    expect(toStatus(Array.from({ length: 10 }, () => ok))).toHaveLength(6)
  })
})
