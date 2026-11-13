import { createServer, type IncomingMessage, type Server } from 'node:http'
import type { AddressInfo } from 'node:net'
import { mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { backendRequest, setConfigDir } from '../../frontend/src/main/backend'
import { BACKEND_PATH_RE } from '../../frontend/src/shared/backend'

describe('BACKEND_PATH_RE', () => {
  it.each([
    '/api/health',
    '/api/llm',
    '/api/llm/key',
    '/api/llm/test',
    '/api/persona',
    '/api/logs',
    '/api/logs?after=3&level=ERROR&q=%E4%BD%A0',
    '/api/events?after=0&type=user.text',
    '/api/memory',
    '/api/memory/summarize',
    '/api/memories?q=%E7%8C%AB&semantic=true',
    '/api/history?before_id=10&limit=50',
    '/api/history/days',
    '/api/history/delete',
    '/api/history/summarized-count?keep_recent=20',
    '/api/history/summarized-count',
    '/api/skills',
    '/api/skills/installed',
    '/api/camera',
    '/api/camera/look',
    '/api/status',
    '/api/status/value',
    '/api/status/history',
    '/api/status/history?offset=40&limit=20',
    '/api/games',
    '/api/games/roulette/start',
    '/api/games/roulette/pet',
    '/api/games/rps/pick',
    '/api/games/rps/chat',
    '/api/games/settings',
    '/api/games/dice/next',
    '/api/games/auction/play',
    '/api/games/taboo/say',
    '/api/games/taboo/quit',
    '/api/voice/key',
    '/api/voice/key?provider=cosyvoice',
    '/api/voice/key?provider=openai',
    '/api/fishing',
    '/api/fishing/command',
    '/api/fishing/settings',
    '/api/fishing/hook',
    '/api/fishing/hud',
    '/api/fishing/hud?after=12',
    '/api/fishing/simulate'
  ])('allows %s', (p) => {
    expect(BACKEND_PATH_RE.test(p)).toBe(true)
  })
  it.each([
    '/api/llm/../x',
    '/api/llm/key/a',
    '/api/llm?x=1',
    '/docs',
    'http://evil/api/llm',
    '/api/llm/',
    '/api/logs?a=1#x',
    '/api/logs?a=/../x',
    '/api/logs/x',
    '/api/memory?x=1',
    '/api/history/days?x=1',
    '/api/history/summarized-count?keep_recent=x',
    '/api/history/summarized-count?keep_recent=1&x=2',
    '/api/pet/image',
    '/api/memories/1',
    '/api/camera?x=1',
    '/api/camera/x',
    '/api/games/x',
    '/api/games/rps/move',
    '/api/games/taboo/pet',
    '/api/games?x=1',
    '/api/voice/key?provider=evil',
    '/api/voice/key?provider=minimax&x=1',
    '/api/fishing/x',
    '/api/fishing/cast',
    '/api/fishing/hud?after=x',
    '/api/fishing?x=1'
  ])(
    'rejects %s',
    (p) => {
      expect(BACKEND_PATH_RE.test(p)).toBe(false)
    }
  )
})

describe('backendRequest', () => {
  let dir: string
  let server: Server | null = null
  const prevAppData = process.env['LOCALAPPDATA']
  let seen: { method?: string; url?: string; auth?: string; body: string }[] = []

  beforeEach(async () => {
    dir = await mkdtemp(join(tmpdir(), 'pet-proxy-'))
    process.env['LOCALAPPDATA'] = dir
    setConfigDir(dir) // no instance.yaml there, so the default pet-kit folder
    seen = []
  })
  afterEach(async () => {
    process.env['LOCALAPPDATA'] = prevAppData
    await new Promise<void>((r) => (server ? server.close(() => r()) : r()))
    server = null
    await rm(dir, { recursive: true, force: true })
  })

  async function startServer(status: number, payload: unknown): Promise<number> {
    server = createServer((req: IncomingMessage, res) => {
      let body = ''
      req.on('data', (c) => (body += c))
      req.on('end', () => {
        seen.push({ method: req.method, url: req.url, auth: req.headers.authorization, body })
        res.writeHead(status, { 'Content-Type': 'application/json' })
        res.end(JSON.stringify(payload))
      })
    })
    await new Promise<void>((r) => server!.listen(0, '127.0.0.1', () => r()))
    return (server!.address() as AddressInfo).port
  }

  async function writeEndpoint(port: number, token = 'tok'): Promise<void> {
    await mkdir(join(dir, 'pet-kit'), { recursive: true })
    await writeFile(join(dir, 'pet-kit', 'backend.json'), JSON.stringify({ port, token }), 'utf8')
  }

  it('reports offline when there is no endpoint file', async () => {
    const r = await backendRequest('GET', '/api/health')
    expect(r).toMatchObject({ ok: false, offline: true })
  })

  it('rejects paths and methods outside the whitelist without sending', async () => {
    expect(await backendRequest('GET', '/api/secret')).toMatchObject({ ok: false, offline: false })
    expect(await backendRequest('PATCH', '/api/llm')).toMatchObject({ ok: false, offline: false })
  })

  it('attaches the bearer token and forwards JSON', async () => {
    const port = await startServer(200, { ok: true })
    await writeEndpoint(port)
    const r = await backendRequest('PUT', '/api/llm', { a: 1 })
    expect(r).toEqual({ ok: true, data: { ok: true } })
    expect(seen).toEqual([{ method: 'PUT', url: '/api/llm', auth: 'Bearer tok', body: '{"a":1}' }])
  })

  it('surfaces backend error detail', async () => {
    const port = await startServer(422, { detail: 'name: 太长' })
    await writeEndpoint(port)
    expect(await backendRequest('PUT', '/api/llm', {})).toEqual({
      ok: false,
      offline: false,
      status: 422,
      error: 'name: 太长'
    })
  })

  it('treats 401 as a stale token', async () => {
    const port = await startServer(401, { detail: 'unauthorized' })
    await writeEndpoint(port)
    expect(await backendRequest('GET', '/api/health')).toMatchObject({ ok: false, offline: true, status: 401 })
  })

  it('reports offline when the port is closed', async () => {
    const port = await startServer(200, {})
    await new Promise<void>((r) => server!.close(() => r()))
    server = null
    await writeEndpoint(port)
    expect(await backendRequest('GET', '/api/health')).toMatchObject({ ok: false, offline: true })
  })
})
