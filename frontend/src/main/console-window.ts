import { BrowserWindow, dialog, ipcMain, nativeImage, shell, type IpcMainEvent, type IpcMainInvokeEvent } from 'electron'
import { HALF_RATIO_RANGE, loadFrontendConfig, updateModelConfig, type FrontendConfig } from './config'
import { basename, dirname, isAbsolute } from 'node:path'
import { access } from 'node:fs/promises'
import { actionsConfigPath, loadActionMap, readModelInventory, saveActionMap } from './actions-config'
import { backendRequest } from './backend'
import { importModel, listModels } from './models'
import { deleteSticker, importStickerFiles, loadStickers, stickerEntries, updateSticker } from './stickers'
import { STICKER_EXTENSIONS, type Sticker, type StickerEntry, type StickerImportResult, type StickerPatch } from '../shared/stickers'
import { loadUiConfig, saveUiConfig } from './ui-config'
import { captureRenderer, type FrontendLog } from './logger'
import { lockDownNavigation, loadPage, secureWebPreferences } from './windows'
import {
  IPC,
  type ModelEntry,
  type ModelLayout,
  type ModelSettings,
  type ModelState,
  type UiSettings
} from '../shared/ipc'
import type { LogEntry, LogQuery, LogsResponse } from '../shared/backend'
import type { HotkeysState } from '../shared/hotkeys'
import type { ActionMap, ModelActions, MotionRef, PlayCommand } from '../shared/agent'

interface ConsoleDeps {
  configPath: string
  configDir: string
  uiConfigPath: string
  publicDir: string
  /** config/stickers.yaml and the folder holding the pictures. */
  stickersPath: string
  stickersDir: string
  /** The sticker list changed (the pet tells the backend the new note names). */
  onStickersChanged(stickers: Sticker[]): void
  onSaved(cfg: FrontendConfig): Promise<void>
  onPreview(layout: ModelLayout): void
  /** Called when the console closes with an unsaved preview applied. */
  onPreviewAbandoned(): void
  onActionsSaved(map: ActionMap): void
  onPlay(cmd: PlayCommand): void
  /** UI settings were saved (pet window picks up the input/bubble style). */
  onUiSaved(ui: UiSettings): void
  log: FrontendLog
  hotkeys: {
    state(): Promise<HotkeysState>
    save(raw: unknown): Promise<HotkeysState>
    suspend(suspended: boolean): void
  }
}

let suspendHotkeys: (s: boolean) => void = () => undefined

let logRef: FrontendLog | null = null

const LOG_LEVELS: readonly LogEntry['level'][] = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']

function parseLogQuery(v: unknown): LogQuery {
  const o = (typeof v === 'object' && v !== null ? v : {}) as Record<string, unknown>
  const str = (x: unknown, max: number): string | undefined => (typeof x === 'string' && x ? x.slice(0, max) : undefined)
  const int = (x: unknown): number | undefined => (Number.isInteger(x) && (x as number) >= 0 ? (x as number) : undefined)
  return {
    after: int(o.after),
    level: LOG_LEVELS.includes(o.level as LogEntry['level']) ? (o.level as LogEntry['level']) : undefined,
    logger: str(o.logger, 200),
    event_id: str(o.event_id, 64),
    q: str(o.q, 200),
    limit: int(o.limit)
  }
}

let consoleWin: BrowserWindow | null = null
let previewDirty = false
// Page a console that is still loading should start on (openConsole(page)).
let pendingPage: string | null = null
let consoleClosedHandler: () => void = () => undefined

function assertFromConsole(e: IpcMainInvokeEvent | IpcMainEvent): void {
  if (!consoleWin || e.sender !== consoleWin.webContents) throw new Error('forbidden')
}

function parseLayout(v: unknown): ModelLayout {
  const o = (typeof v === 'object' && v !== null ? v : {}) as Record<string, unknown>
  if (o.scale !== 'auto' && !(typeof o.scale === 'number' && o.scale > 0)) {
    throw new Error('scale 必须是 "auto" 或正数')
  }
  if (o.framing !== 'full' && o.framing !== 'half') throw new Error('framing 必须是 "full" 或 "half"')
  const { min, max } = HALF_RATIO_RANGE
  if (typeof o.halfRatio !== 'number' || o.halfRatio < min || o.halfRatio > max) {
    throw new Error(`halfRatio 必须在 [${min}, ${max}]`)
  }
  return { scale: o.scale, framing: o.framing, halfRatio: o.halfRatio }
}

function parseModelSettings(v: unknown): ModelSettings {
  const o = (typeof v === 'object' && v !== null ? v : {}) as Record<string, unknown>
  if (typeof o.path !== 'string') throw new Error('path 必须是字符串')
  return { path: o.path, ...parseLayout(v) }
}

function parsePlay(v: unknown): PlayCommand {
  const o = (typeof v === 'object' && v !== null ? v : {}) as Record<string, unknown>
  const cmd: PlayCommand = {}
  if (o.stop === true) cmd.stop = true
  if (typeof o.expression === 'string' && o.expression.length <= 64) cmd.expression = o.expression
  const m = o.motion as Partial<MotionRef> | null | undefined
  if (m && typeof m.group === 'string' && m.group.length <= 64 && Number.isInteger(m.index) && (m.index as number) >= 0) {
    cmd.motion = { group: m.group, index: m.index as number }
    if (typeof m.expression === 'string' && m.expression !== '' && m.expression.length <= 64) {
      cmd.motion.expression = m.expression
    }
    if (m.loop === true) cmd.motion.loop = true
  }
  return cmd
}

export function registerConsole(deps: ConsoleDeps): void {
  logRef = deps.log
  suspendHotkeys = deps.hotkeys.suspend
  ipcMain.handle(IPC.consoleHotkeys, async (e): Promise<HotkeysState> => {
    assertFromConsole(e)
    return deps.hotkeys.state()
  })
  ipcMain.handle(IPC.consoleSaveHotkeys, async (e, raw: unknown): Promise<HotkeysState> => {
    assertFromConsole(e)
    return deps.hotkeys.save(raw)
  })
  ipcMain.on(IPC.consoleSuspendHotkeys, (e, s: unknown) => {
    if (consoleWin && e.sender === consoleWin.webContents) deps.hotkeys.suspend(s === true)
  })
  ipcMain.handle(IPC.consoleLogs, (e, q: unknown): LogsResponse => {
    assertFromConsole(e)
    return { entries: deps.log.query(parseLogQuery(q)), last_seq: deps.log.lastSeq, log_dir: deps.log.dir }
  })
  ipcMain.handle(IPC.consoleOpenLogs, async (e): Promise<string> => {
    assertFromConsole(e)
    return shell.openPath(deps.log.dir)
  })
  ipcMain.handle(IPC.consoleShowFile, async (e, p: unknown): Promise<string> => {
    assertFromConsole(e)
    // Only reveals (never opens/executes) an existing data file of ours.
    if (typeof p !== 'string' || !isAbsolute(p) || !/\.(db|jsonl)$/i.test(p)) return '只能定位 .db 或 .jsonl 文件'
    try {
      await access(p)
    } catch {
      return `文件不存在：${p}`
    }
    shell.showItemInFolder(p)
    return ''
  })

  ipcMain.handle(IPC.consoleModelState, async (e): Promise<ModelState> => {
    assertFromConsole(e)
    const cfg = await loadFrontendConfig(deps.configPath)
    return { models: await listModels(deps.publicDir), current: { ...cfg.model } }
  })

  ipcMain.handle(IPC.consoleImportModel, async (e): Promise<ModelEntry | null> => {
    assertFromConsole(e)
    const res = await dialog.showOpenDialog(consoleWin!, {
      title: '选择 Live2D 模型文件',
      filters: [{ name: 'Live2D Cubism 4 模型', extensions: ['json'] }],
      properties: ['openFile']
    })
    if (res.canceled || res.filePaths.length === 0) return null
    return importModel(res.filePaths[0], deps.publicDir)
  })

  ipcMain.handle(IPC.consoleSaveModel, async (e, raw: unknown): Promise<void> => {
    assertFromConsole(e)
    const cfg = await updateModelConfig(deps.configPath, parseModelSettings(raw))
    previewDirty = false
    await deps.onSaved(cfg)
  })

  ipcMain.on(IPC.consolePreviewModel, (e, raw: unknown) => {
    try {
      assertFromConsole(e)
      const layout = parseLayout(raw)
      previewDirty = true
      deps.onPreview(layout)
    } catch (err) {
      console.warn('[console] preview rejected:', err instanceof Error ? err.message : err)
    }
  })

  ipcMain.handle(IPC.consoleGetUi, async (e): Promise<UiSettings> => {
    assertFromConsole(e)
    return loadUiConfig(deps.uiConfigPath)
  })

  ipcMain.handle(IPC.consoleSaveUi, async (e, raw: unknown): Promise<UiSettings> => {
    assertFromConsole(e)
    const ui = await saveUiConfig(deps.uiConfigPath, raw)
    deps.onUiSaved(ui)
    return ui
  })

  ipcMain.handle(IPC.consoleBackend, async (e, method: unknown, path: unknown, body: unknown) => {
    assertFromConsole(e)
    return backendRequest(method, path, body)
  })

  ipcMain.handle(IPC.consoleModelActions, async (e): Promise<ModelActions> => {
    assertFromConsole(e)
    const { path } = (await loadFrontendConfig(deps.configPath)).model
    const file = actionsConfigPath(deps.configDir, path)
    return {
      model: file ? basename(dirname(path.replace(/\\/g, '/'))) : null,
      available: await readModelInventory(deps.publicDir, path),
      map: await loadActionMap(file)
    }
  })

  ipcMain.handle(IPC.consoleSaveModelActions, async (e, raw: unknown): Promise<ActionMap> => {
    assertFromConsole(e)
    const file = actionsConfigPath(deps.configDir, (await loadFrontendConfig(deps.configPath)).model.path)
    if (!file) throw new Error('当前没有可用的模型')
    const map = await saveActionMap(file, raw)
    deps.onActionsSaved(map)
    return map
  })

  ipcMain.on(IPC.consolePlay, (e, raw: unknown) => {
    try {
      assertFromConsole(e)
      deps.onPlay(parsePlay(raw))
    } catch (err) {
      console.warn('[console] play rejected:', err instanceof Error ? err.message : err)
    }
  })

  // Small previews for the list; formats Electron can't decode (GIF, WebP) are shown as they are.
  const shrink = (buf: Buffer): string | null => {
    const img = nativeImage.createFromBuffer(buf)
    return img.isEmpty() ? null : img.resize({ height: 144 }).toDataURL()
  }
  const entries = (list: Sticker[]): Promise<StickerEntry[]> => stickerEntries(deps.stickersDir, list, shrink)
  // One sticker operation at a time: each one reads, changes and rewrites the same index file.
  let stickerQueue: Promise<unknown> = Promise.resolve()
  const queued = <T>(fn: () => Promise<T>): Promise<T> => {
    const next = stickerQueue.then(fn, fn)
    stickerQueue = next.catch(() => undefined)
    return next
  }
  const changed = async (list: Sticker[]): Promise<StickerEntry[]> => {
    deps.onStickersChanged(list)
    return entries(list)
  }

  ipcMain.handle(IPC.consoleStickers, async (e): Promise<StickerEntry[]> => {
    assertFromConsole(e)
    return queued(async () => entries(await loadStickers(deps.stickersPath)))
  })

  ipcMain.handle(IPC.consoleImportStickers, async (e): Promise<StickerImportResult> => {
    assertFromConsole(e)
    const res = await dialog.showOpenDialog(consoleWin!, {
      title: '选择表情包图片',
      filters: [{ name: '图片', extensions: [...STICKER_EXTENSIONS] }],
      properties: ['openFile', 'multiSelections']
    })
    return queued(async () => {
      if (res.canceled || res.filePaths.length === 0) {
        return { stickers: await entries(await loadStickers(deps.stickersPath)), errors: [] }
      }
      const r = await importStickerFiles(res.filePaths, deps.stickersPath, deps.stickersDir)
      return { stickers: await changed(r.stickers), errors: r.errors }
    })
  })

  ipcMain.handle(IPC.consoleUpdateSticker, async (e, id: unknown, raw: unknown): Promise<StickerEntry[]> => {
    assertFromConsole(e)
    if (typeof id !== 'string') throw new Error('参数有误')
    const o = (typeof raw === 'object' && raw !== null ? raw : {}) as Record<string, unknown>
    const patch: StickerPatch = {}
    if (typeof o.name === 'string') patch.name = o.name
    if (typeof o.enabled === 'boolean') patch.enabled = o.enabled
    return queued(async () => changed(await updateSticker(deps.stickersPath, id, patch)))
  })

  ipcMain.handle(IPC.consoleDeleteSticker, async (e, id: unknown): Promise<StickerEntry[]> => {
    assertFromConsole(e)
    if (typeof id !== 'string') throw new Error('参数有误')
    return queued(async () => changed(await deleteSticker(deps.stickersPath, deps.stickersDir, id)))
  })

  ipcMain.handle(IPC.consoleInitialPage, (e): string | null => {
    assertFromConsole(e)
    const page = pendingPage
    pendingPage = null
    return page
  })

  consoleClosedHandler = () => {
    if (previewDirty) deps.onPreviewAbandoned()
    previewDirty = false
  }
}

/** Opens (or focuses) the console; `page` = the menu key of the page to show. */
export function openConsole(page?: string): void {
  const target = page && /^[a-z]{1,20}$/.test(page) ? page : null
  if (consoleWin && !consoleWin.isDestroyed()) {
    if (consoleWin.isMinimized()) consoleWin.restore()
    consoleWin.show()
    consoleWin.focus()
    if (target) consoleWin.webContents.send(IPC.consoleNavigate, target)
    return
  }
  pendingPage = target
  consoleWin = new BrowserWindow({
    width: 960,
    height: 640,
    minWidth: 760,
    minHeight: 500,
    autoHideMenuBar: true,
    title: '桌宠控制台',
    show: false,
    backgroundColor: '#18181c',
    webPreferences: secureWebPreferences('console')
  })
  consoleWin.setMenu(null)
  lockDownNavigation(consoleWin.webContents)
  consoleWin.on('closed', () => suspendHotkeys(false))
  // A crashed or reloaded console can't finish its recording; never leave hotkeys suspended.
  consoleWin.webContents.on('render-process-gone', () => suspendHotkeys(false))
  consoleWin.webContents.on('did-start-loading', () => suspendHotkeys(false))
  if (logRef) captureRenderer(logRef, consoleWin.webContents, 'console')
  consoleWin.once('ready-to-show', () => consoleWin?.show())
  consoleWin.on('closed', () => {
    consoleWin = null
    consoleClosedHandler()
  })
  loadPage(consoleWin, 'console')
}
