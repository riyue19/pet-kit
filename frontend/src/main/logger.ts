import { appendFile, mkdir, readdir, unlink } from 'node:fs/promises'
import { join } from 'node:path'
import type { LogEntry, LogQuery } from '../shared/backend'

const RING_SIZE = 2000
const KEEP_DAYS = 14
const MAX_QUERY = 1000
const LEVELS: Record<LogEntry['level'], number> = { DEBUG: 10, INFO: 20, WARNING: 30, ERROR: 40, CRITICAL: 50 }
const FILE_RE = /^frontend-(\d{4}-\d{2}-\d{2})\.jsonl$/
// Browser notices that look like errors but aren't (layout feedback loops resolved by the next frame).
const BENIGN_RENDERER_MESSAGES = [/^ResizeObserver loop (completed with undelivered notifications|limit exceeded)/]
const SECRET_RES = [/\bsk-[A-Za-z0-9_-]{8,}/g, /(bearer\s+)[A-Za-z0-9._-]{8,}/gi]

export interface LogSite {
  file?: string
  line?: number
  func?: string
  exc?: string | null
  event_id?: string | null
}

export function redact(text: string): string {
  return SECRET_RES.reduce((t, re) => t.replace(re, (_m, prefix?: string) => (typeof prefix === 'string' ? `${prefix}***` : '***')), text)
}

function localDate(d: Date): string {
  const p = (n: number): string => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

/** Best-effort "file:line in func" from a stack trace: frame `skip` after the message line. */
export function callerSite(stack: string | undefined, skip = 0): LogSite {
  const frames = (stack ?? '').split('\n').slice(1).filter((l) => l.trim().startsWith('at '))
  const frame = frames[skip]
  if (!frame) return {}
  const m = /at (?:(.+?) \()?(.+?):(\d+):\d+\)?$/.exec(frame.trim())
  if (!m) return {}
  const file = m[2].replace(/\\/g, '/').replace(/^.*?\/(src|out|node_modules)\//, '$1/')
  return { func: m[1] ?? '', file, line: Number(m[3]) }
}

/**
 * Frontend log: JSON lines in <dir>/frontend-YYYY-MM-DD.jsonl (14 days kept) plus an in-memory ring for the
 * console's 日志 page. Same entry shape as the backend (backend/core/logs.py).
 */
export class FrontendLog {
  private ring: LogEntry[] = []
  private seq = 0
  private writes: Promise<void> = Promise.resolve()
  private dirReady: Promise<void> | null = null

  constructor(
    readonly dir: string,
    private readonly now: () => Date = () => new Date(),
    private readonly persist = true
  ) {}

  write(level: LogEntry['level'], logger: string, msg: string, site: LogSite = {}): LogEntry {
    const entry: LogEntry = {
      seq: ++this.seq,
      ts: Math.round(this.now().getTime()) / 1000,
      level,
      logger,
      file: site.file ?? '',
      line: site.line ?? 0,
      func: site.func ?? '',
      msg: redact(msg).slice(0, 4000),
      event_id: site.event_id ?? null,
      exc: site.exc ? redact(site.exc).slice(0, 16000) : null
    }
    this.ring.push(entry)
    if (this.ring.length > RING_SIZE) this.ring.shift()
    if (this.persist) this.append(entry)
    return entry
  }

  get lastSeq(): number {
    return this.seq
  }

  query(q: LogQuery = {}): LogEntry[] {
    const floor = LEVELS[q.level ?? 'DEBUG'] ?? 0
    const needle = q.q?.toLowerCase()
    const limit = Math.min(Math.max(q.limit ?? 200, 1), MAX_QUERY)
    const out: LogEntry[] = []
    for (let i = this.ring.length - 1; i >= 0 && out.length < limit; i--) {
      const e = this.ring[i]
      if (e.seq <= (q.after ?? 0)) break
      if (LEVELS[e.level] < floor) continue
      if (q.logger && !e.logger.startsWith(q.logger)) continue
      if (q.event_id && e.event_id !== q.event_id) continue
      if (needle && !e.msg.toLowerCase().includes(needle) && !(e.exc ?? '').toLowerCase().includes(needle)) continue
      out.push(e)
    }
    return out.reverse()
  }

  /** Deletes log files older than KEEP_DAYS. */
  async prune(): Promise<void> {
    let names: string[]
    try {
      names = await readdir(this.dir)
    } catch {
      return
    }
    const cutoff = localDate(new Date(this.now().getTime() - KEEP_DAYS * 86400_000))
    await Promise.all(
      names
        .filter((n) => (FILE_RE.exec(n)?.[1] ?? '9999') < cutoff)
        .map((n) => unlink(join(this.dir, n)).catch(() => undefined))
    )
  }

  /** Resolves once queued writes are on disk (tests, shutdown). */
  flush(): Promise<void> {
    return this.writes
  }

  private append(entry: LogEntry): void {
    const file = join(this.dir, `frontend-${localDate(new Date(entry.ts * 1000))}.jsonl`)
    const line = JSON.stringify(entry) + '\n'
    // Serialized so lines never interleave; a failed write must not break logging.
    this.writes = this.writes
      .then(() => (this.dirReady ??= mkdir(this.dir, { recursive: true }).then(() => undefined)))
      .then(() => appendFile(file, line, 'utf8'))
      .catch(() => {
        // Folder missing (deleted while running, or mkdir failed): recreate it on the next write.
        this.dirReady = null
      })
  }
}

function formatArgs(args: unknown[]): { msg: string; exc: string | null } {
  let exc: string | null = null
  const parts = args.map((a) => {
    if (a instanceof Error) {
      exc ??= a.stack ?? `${a.name}: ${a.message}`
      return `${a.name}: ${a.message}`
    }
    if (typeof a === 'string') return a
    try {
      return JSON.stringify(a)
    } catch {
      return String(a)
    }
  })
  return { msg: parts.join(' '), exc }
}

/** Mirrors main-process console.warn/error into the log (original output kept) and records crashes. */
export function captureMainProcess(log: FrontendLog): void {
  const wrap = (level: 'WARNING' | 'ERROR', original: (...a: unknown[]) => void) => {
    const wrapped = (...args: unknown[]): void => {
      original(...args)
      const { msg, exc } = formatArgs(args)
      // Stack starting at whoever called console.warn/error, however the code is bundled.
      const holder: { stack?: string } = {}
      Error.captureStackTrace(holder, wrapped)
      log.write(level, 'main', msg, { ...callerSite(holder.stack), exc })
    }
    return wrapped
  }
  console.warn = wrap('WARNING', console.warn.bind(console))
  console.error = wrap('ERROR', console.error.bind(console))
  // *Monitor: observes without changing Electron's default crash handling.
  process.on('uncaughtExceptionMonitor', (err) => {
    log.write('CRITICAL', 'main', `未捕获异常：${err.message}`, { ...callerSite(err.stack), exc: err.stack ?? null })
  })
  process.on('unhandledRejection', (reason) => {
    const err = reason instanceof Error ? reason : null
    log.write('ERROR', 'main', `未处理的 Promise 拒绝：${err ? err.message : String(reason)}`, {
      ...callerSite(err?.stack),
      exc: err?.stack ?? null
    })
  })
}

/** Records warnings/errors a renderer prints (including uncaught exceptions) and renderer crashes. */
export function captureRenderer(log: FrontendLog, contents: Electron.WebContents, logger: 'pet' | 'console' | 'code'): void {
  contents.on('console-message', (e) => {
    const level = e.level === 'error' ? 'ERROR' : e.level === 'warning' ? 'WARNING' : null
    if (!level || BENIGN_RENDERER_MESSAGES.some((re) => re.test(e.message))) return
    const file = (e.sourceId ?? '').replace(/^.*?\/(src|assets)\//, '$1/').replace(/\?.*$/, '')
    log.write(level, logger, e.message, { file, line: e.lineNumber })
  })
  contents.on('render-process-gone', (_e, details) => {
    log.write('CRITICAL', logger, `渲染进程退出：${details.reason}（exitCode ${details.exitCode}）`)
  })
  contents.on('preload-error', (_e, path, error) => {
    log.write('ERROR', logger, `preload 出错：${error.message}`, { file: path, exc: error.stack ?? null })
  })
}
