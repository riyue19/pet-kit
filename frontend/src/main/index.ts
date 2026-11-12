import { join, resolve } from 'node:path'
import { app, BrowserWindow, dialog, globalShortcut, ipcMain, Menu, nativeImage, screen, session, Tray } from 'electron'
import { actionsConfigPath, loadActionMap } from './actions-config'
import { loadStickers, stickerDataUrl } from './stickers'
import type { Sticker } from '../shared/stickers'
import { loadFrontendConfig, type FrontendConfig } from './config'
import { PetLink } from './pet-link'
import { captureMainProcess, captureRenderer, FrontendLog } from './logger'
import { HotkeyManager, readHotkeysFile, saveHotkeys } from './hotkeys'
import { backendRequest, uploadImage } from './backend'
import { registerCodeIpc, showCode } from './code-window'
import type { Hotkey, HotkeysState } from '../shared/hotkeys'
import type { VoiceState } from '../shared/backend'
import { openConsole, registerConsole } from './console-window'
import { lockDownNavigation, loadPage, secureWebPreferences } from './windows'
import {
  clampDragToWorkArea,
  clampToWorkArea,
  defaultWindowPosition,
  dragPosition,
  screenToWindow,
  type Point
} from '../shared/geometry'
import { IPC, type ModelLayout, type PetState, type RendererConfig, type UiSettings } from '../shared/ipc'
import { DEFAULT_UI, applyCssProposal, loadUiConfig, saveUiConfig } from './ui-config'
import type { PetStyle } from '../shared/ui-style'
import { FISHING_OFF, type FishingState } from '../shared/fishing'
import {
  DEFAULT_ACTION_MAP,
  MAX_CHAT_CHARS,
  MAX_IMAGE_DATA_URL_CHARS,
  MAX_MIC_FRAME_BYTES,
  type ActionMap,
  type AgentOutput,
  type VoiceMessage
} from '../shared/agent'

const DRAG_INTERVAL_MS = 16
const DEFAULT_MARGIN = 16

let win: BrowserWindow | null = null
let tray: Tray | null = null
let cursorTimer: NodeJS.Timeout | null = null
let dragTimer: NodeJS.Timeout | null = null
let ignoringMouse = true
let rendererConfig: RendererConfig
// Window size from frontend.yaml; the window grows past it (never below) when the model is scaled up.
let baseWindow = { width: 400, height: 600 }
const MAX_WINDOW_PX = 8000
let actions: ActionMap = DEFAULT_ACTION_MAP
let stickers: Sticker[] = []
let petStyle: PetStyle = pickPetStyle(DEFAULT_UI)

function pickPetStyle(ui: UiSettings): PetStyle {
  return { inputStyle: ui.inputStyle, bubbleStyle: ui.bubbleStyle, petCss: ui.petCss }
}
// Shares %LOCALAPPDATA%/pet-kit/logs with the backend's backend.jsonl.
// Stack traces (and so log file:line) point at src/*.ts instead of the bundled out/main/index.js.
process.setSourceMapsEnabled(true)
const flog = new FrontendLog(join(process.env['LOCALAPPDATA'] ?? app.getPath('userData'), 'pet-kit', 'logs'))
captureMainProcess(flog)
// Mirrors the backend's asr.listen; the microphone permission is only granted while this is true.
let micAllowed = false
let cameraOn = false
let cameraPresent: boolean | null = null
let cameraEnabled = false
// Last fishing.state from the backend: drives the menu switch and is replayed to a reloaded pet window.
let fishingState: FishingState = FISHING_OFF
const link = new PetLink({
  labels: () => ({
    expressions: Object.keys(actions.expressions),
    motions: Object.keys(actions.motions),
    stickers: stickers.filter((s) => s.enabled).map((s) => s.name)
  }),
  onOutput: (o) => void deliverOutput(o),
  onStatus: (st) => sendToPet(IPC.linkStatus, st),
  onVoice: (m: VoiceMessage) => {
    if (m.type === 'mic') micAllowed = m.listen
    if (m.type === 'camera') {
      cameraOn = m.on
      cameraPresent = m.present
      cameraEnabled = m.enabled
      refreshTray()
    }
    sendToPet(IPC.voice, m)
  },
  onAudio: (chunk) => sendToPet(IPC.audio, chunk),
  onFishing: (m) => {
    if (m.type === 'fishing.state') {
      const changed = m.enabled !== fishingState.enabled || m.placed !== fishingState.placed
      fishingState = m
      if (changed) refreshTray()
    }
    sendToPet(IPC.fishing, m)
  },
  onCode: (code) => showCode(code, flog),
  onSetCss: (p) => void proposeCss(p.css, p.replace),
  // The pet proposed a mini game: nothing starts unless the user says yes (closing the box = no).
  onGameInvite: (p) => {
    void dialog
      .showMessageBox({
        type: 'question',
        title: '桌宠',
        message: `${p.pet}想和你玩「${p.name}」`,
        detail: '选「来一局」会打开控制台的小游戏页。',
        buttons: ['下次吧', '来一局'],
        defaultId: 1,
        cancelId: 0,
        noLink: true
      })
      .then((r) => {
        if (r.response === 1) openConsole('games')
      })
      .catch(() => undefined)
  },
  // Dangerous skills (config/skills.yaml confirm: true) run only after an explicit "允许"; closing the box denies.
  onConfirm: (req) => {
    void dialog
      .showMessageBox({
        type: 'question',
        title: '桌宠',
        message: req.title,
        detail: req.detail,
        buttons: ['拒绝', '允许'],
        defaultId: 0,
        cancelId: 0,
        noLink: true
      })
      .then((r) => link.sendConfirm(req.id, r.response === 1))
      .catch(() => link.sendConfirm(req.id, false))
  }
})

function configDir(): string {
  return resolve(app.getAppPath(), '../config')
}

function configPath(): string {
  return join(configDir(), 'frontend.yaml')
}

function publicDir(): string {
  return join(app.getAppPath(), 'public')
}

function stickersPath(): string {
  return join(configDir(), 'stickers.yaml')
}

/** Sticker pictures live with the other local data (data/ is not in git). */
function stickersDir(): string {
  return resolve(app.getAppPath(), '../data/stickers')
}

/** Attaches the picture of the sticker the model named, then hands the reply to the pet window. */
async function deliverOutput(o: AgentOutput): Promise<void> {
  const s = o.sticker ? stickers.find((x) => x.enabled && x.name === o.sticker) : undefined
  if (s) {
    const url = await stickerDataUrl(stickersDir(), s)
    if (url) o = { ...o, stickerUrl: url }
    else console.warn(`[stickers] 表情包「${s.name}」的图片读不到，这次只显示文字`)
  }
  sendToPet(IPC.output, o)
}

function hotkeysPath(): string {
  return join(configDir(), 'hotkeys.yaml')
}

function toggleVisible(): void {
  if (!win) return
  if (win.isVisible()) win.hide()
  else win.showInactive()
}

let micToggling = false

async function toggleMic(): Promise<void> {
  // A second press while the first is still talking to the backend would read the same state and undo nothing.
  if (micToggling) return
  micToggling = true
  try {
    await toggleMicOnce()
  } finally {
    micToggling = false
  }
}

async function toggleMicOnce(): Promise<void> {
  const cur = await backendRequest('GET', '/api/voice')
  if (!cur.ok) return sendToPet(IPC.output, localReply(`（无法切换麦克风：${cur.error}）`))
  const state = cur.data as VoiceState
  if (!state.models_ready) return sendToPet(IPC.output, localReply('（语音识别模型未下载）'))
  const listen = !state.asr.listen
  const res = await backendRequest('PUT', '/api/voice', { asr: { ...state.asr, listen }, tts: state.tts })
  sendToPet(IPC.output, localReply(res.ok ? (listen ? '（麦克风已开启）' : '（麦克风已关闭）') : `（切换失败：${res.error}）`))
}

let cameraToggling = false

/** Flips camera.enabled in the backend (same switch as the console's 摄像头 card). */
async function toggleCamera(): Promise<void> {
  if (cameraToggling) return
  cameraToggling = true
  try {
    const cur = await backendRequest('GET', '/api/camera')
    if (!cur.ok) return sendToPet(IPC.output, localReply(`（无法切换摄像头：${cur.error}）`))
    const cfg = (cur.data as { camera: { enabled: boolean } }).camera
    const enabled = !cfg.enabled
    const res = await backendRequest('PUT', '/api/camera', { ...cfg, enabled })
    sendToPet(IPC.output, localReply(res.ok ? (enabled ? '（摄像头已开启，左上角会显示红色提示）' : '（摄像头已关闭）') : `（切换失败：${res.error}）`))
  } finally {
    cameraToggling = false
  }
}

const HOTKEY_DEBOUNCE_MS = 300
const lastFired = new Map<string, number>()

async function allSensesOff(): Promise<void> {
  // Stop the mic locally at once, even if the backend can't be reached.
  sendToPet(IPC.voice, { type: 'mic', listen: false })
  const res = await backendRequest('POST', '/api/privacy/off')
  sendToPet(IPC.output, localReply(res.ok ? '（已关闭麦克风、屏幕感知和摄像头）' : `（后端未连接，已在本地关闭麦克风：${res.error}）`))
}

function onHotkey(h: Hotkey): void {
  if (h.action === 'builtin' && h.builtin === 'push_to_talk') {
    // Fires again and again while held (key repeat); the backend ignores repeats and detects the release.
    if (!link.sendPtt(h.id) && Date.now() - (lastFired.get(h.id) ?? 0) > 3000) {
      lastFired.set(h.id, Date.now())
      sendToPet(IPC.output, localReply('（后端未连接）'))
    }
    return
  }
  // Holding a combo can auto-repeat; one press per 300 ms is plenty.
  const now = Date.now()
  if (now - (lastFired.get(h.id) ?? 0) < HOTKEY_DEBOUNCE_MS) return
  lastFired.set(h.id, now)
  // Only the hotkey id is logged; no key data exists to log.
  flog.write('INFO', 'main', `快捷键 ${h.id}（${h.name || h.keys}）`)
  if (h.action === 'builtin') {
    switch (h.builtin) {
      case 'toggle_visible':
        return toggleVisible()
      case 'open_console':
        return openConsole()
      case 'stop_speaking':
        sendToPet(IPC.voice, { type: 'interrupt' })
        link.sendStop()
        return
      case 'toggle_mic':
        void toggleMic()
        return
      case 'toggle_camera':
        void toggleCamera()
        return
    }
    return
  }
  if (!link.sendEvent('hotkey.pressed', { id: h.id })) {
    sendToPet(IPC.output, localReply('（后端未连接）'))
  }
}

const hotkeys = new HotkeyManager(globalShortcut, onHotkey)

let hotkeysError: string | null = null

function hotkeysState(): HotkeysState {
  return { ...hotkeys.current, file: hotkeysPath(), error: hotkeysError }
}

/** Reads hotkeys.yaml from disk and (re)registers when it changed, e.g. after a hand edit. */
async function loadAndApplyHotkeys(): Promise<void> {
  try {
    const { hotkeys: list, skipped } = await readHotkeysFile(hotkeysPath())
    hotkeysError = skipped.length ? `以下项无效，已跳过：${skipped.join('；')}` : null
    if (JSON.stringify(list) !== JSON.stringify(hotkeys.current.hotkeys)) hotkeys.apply(list)
  } catch (e) {
    // Keep what is registered; the console shows the error and refuses to overwrite blindly.
    hotkeysError = e instanceof Error ? e.message : String(e)
  }
  if (hotkeysError) console.warn('[hotkeys]', hotkeysError)
}

function uiConfigPath(): string {
  return resolve(app.getAppPath(), '../config/console.yaml')
}

function toRendererConfig(cfg: FrontendConfig): RendererConfig {
  const { path, scale, framing, halfRatio } = cfg.model
  return { modelPath: path, scale, framing, halfRatio, alphaThreshold: cfg.hitTest.alphaThreshold, actions, petStyle }
}

async function loadActionsFor(modelPath: string): Promise<void> {
  try {
    actions = await loadActionMap(actionsConfigPath(configDir(), modelPath))
  } catch (e) {
    actions = DEFAULT_ACTION_MAP
    console.warn('[pet] 表情映射加载失败，已忽略：', e instanceof Error ? e.message : e)
  }
}

let styleProposalOpen = false

/**
 * The pet proposed CSS for its own look. It is applied to the pet window at once (not saved) so the user can see it,
 * then asked: keep (saved, with the old look stored as a scheme for undo) or revert.
 */
async function proposeCss(css: string, replace: boolean): Promise<void> {
  if (styleProposalOpen) return // one question at a time; a second proposal while the dialog is open is dropped
  styleProposalOpen = true
  try {
    const before = await loadUiConfig(uiConfigPath())
    const next = applyCssProposal(before, css, replace, new Date())
    if (!next.ok) {
      sendToPet(IPC.output, localReply(`（样式没改成：${next.error}）`))
      return
    }
    sendToPet(IPC.petStyle, pickPetStyle(next.ui))
    const r = await dialog.showMessageBox({
      type: 'question',
      title: '桌宠',
      message: replace && !css ? '桌宠想恢复默认外观，要保留吗？' : '桌宠想改自己的外观，已临时应用，要保留吗？',
      detail:
        `${css.slice(0, 600)}${css.length > 600 ? '\n…' : ''}\n\n` +
        '选「还原」不会改动任何设置；选「保留」会保存，旧样式会存成「样式方案」，可在控制台「界面」页恢复。',
      buttons: ['还原', '保留'],
      defaultId: 0,
      cancelId: 0,
      noLink: true
    })
    if (r.response === 1) {
      const saved = await saveUiConfig(uiConfigPath(), next.ui)
      petStyle = pickPetStyle(saved)
      rendererConfig = { ...rendererConfig, petStyle }
      sendToPet(IPC.petStyle, petStyle)
    } else {
      sendToPet(IPC.petStyle, pickPetStyle(before))
    }
  } catch (e) {
    console.warn('[pet] 修改样式失败：', e instanceof Error ? e.message : e)
    sendToPet(IPC.petStyle, petStyle)
    sendToPet(IPC.output, localReply('（样式没改成，详情见日志）'))
  } finally {
    styleProposalOpen = false
  }
}

function sendToPet(channel: string, payload: unknown): void {
  if (win && !win.isDestroyed()) win.webContents.send(channel, payload)
}

function sendLayout(layout: ModelLayout): void {
  sendToPet(IPC.layout, layout)
}

function localReply(text: string): AgentOutput {
  return { text, expression: null, motion: null, tool_calls: [] }
}

function initialPosition(cfg: FrontendConfig): Point {
  const size = { width: cfg.window.width, height: cfg.window.height }
  if (cfg.window.x !== null && cfg.window.y !== null) {
    const pos = { x: cfg.window.x, y: cfg.window.y }
    const area = screen.getDisplayMatching({ ...pos, ...size }).workArea
    return clampToWorkArea({ ...pos, ...size }, area)
  }
  return defaultWindowPosition(size, screen.getPrimaryDisplay().workArea, DEFAULT_MARGIN)
}

/** Rescue for a pet dragged (almost) off screen. */
function resetPosition(): void {
  if (!win || win.isDestroyed()) return
  const { width, height } = win.getBounds()
  const pos = defaultWindowPosition({ width, height }, screen.getPrimaryDisplay().workArea, DEFAULT_MARGIN)
  win.setBounds({ ...pos, width, height })
  win.showInactive()
}

function setIgnoreMouse(ignore: boolean, force = false): void {
  if (!win || (!force && ignore === ignoringMouse)) return
  ignoringMouse = ignore
  if (!app.isPackaged) console.log(`[pet] click-through ${ignore ? 'ON  (鼠标穿透)' : 'OFF (模型接收鼠标)'}`)
  // forward:true keeps mousemove events flowing to the renderer so it can hit-test while ignored.
  win.setIgnoreMouseEvents(ignore, { forward: true })
}

let lastCursor: Point | null = null

function startCursorStream(fps: number): void {
  cursorTimer = setInterval(() => {
    if (!win || win.isDestroyed()) return
    const p = screenToWindow(screen.getCursorScreenPoint(), win.getBounds())
    if (lastCursor && lastCursor.x === p.x && lastCursor.y === p.y) return
    lastCursor = p
    win.webContents.send(IPC.cursor, p)
  }, Math.round(1000 / fps))
}

function startDrag(): void {
  if (!win || dragTimer) return
  // The user grabbed the pet: bring it back in front of other always-on-top windows (no focus change).
  win.moveTop()
  const bounds = win.getBounds()
  const startWindow = { x: bounds.x, y: bounds.y }
  const startCursor = screen.getCursorScreenPoint()
  dragTimer = setInterval(() => {
    if (!win || win.isDestroyed()) return stopDrag()
    const cursor = screen.getCursorScreenPoint()
    const next = dragPosition(startWindow, startCursor, cursor)
    const area = screen.getDisplayNearestPoint(cursor).workArea
    const pos = clampDragToWorkArea({ ...next, width: bounds.width, height: bounds.height }, area)
    // setBounds with fixed size: setPosition alone can resize the window when crossing mixed-DPI monitors.
    win.setBounds({ ...pos, width: bounds.width, height: bounds.height })
  }, DRAG_INTERVAL_MS)
}

/** Resizes the pet window to max(configured size, what the model needs), keeping its bottom-center fixed. */
function fitWindow(need: unknown): void {
  if (!win || win.isDestroyed() || dragTimer) return
  const n = need as { width?: unknown; height?: unknown } | null
  const valid = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v) && v > 0
  const width = Math.min(MAX_WINDOW_PX, Math.max(baseWindow.width, n && valid(n.width) ? Math.ceil(n.width) : 0))
  const height = Math.min(MAX_WINDOW_PX, Math.max(baseWindow.height, n && valid(n.height) ? Math.ceil(n.height) : 0))
  const b = win.getBounds()
  if (b.width === width && b.height === height) return
  const x = Math.round(b.x + (b.width - width) / 2)
  const y = b.y + b.height - height
  const area = screen.getDisplayNearestPoint({ x: b.x + b.width / 2, y: b.y + b.height / 2 }).workArea
  const pos = clampDragToWorkArea({ x, y, width, height }, area)
  win.setBounds({ ...pos, width, height })
}

function stopDrag(): void {
  if (dragTimer) clearInterval(dragTimer)
  dragTimer = null
}

// Renderer state is lost on reload/crash; reset main-side state so the window never ends up
// blocking clicks while the renderer believes it is click-through.
function resetInteractionState(): void {
  stopDrag()
  setIgnoreMouse(true, true)
  // A reloaded/crashed renderer can't report the end of playback; otherwise the backend's echo guard stays on.
  link.sendPlayback(false)
}

function trayIcon(): Electron.NativeImage {
  const size = 32
  const buf = Buffer.alloc(size * size * 4)
  const c = (size - 1) / 2
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      if (Math.hypot(x - c, y - c) > c) continue
      const i = (y * size + x) * 4
      buf[i] = 0xb4 // B
      buf[i + 1] = 0x8c // G
      buf[i + 2] = 0xff // R
      buf[i + 3] = 0xff // A
    }
  }
  return nativeImage.createFromBitmap(buf, { width: size, height: size })
}

function buildMenu(): Menu {
  return Menu.buildFromTemplate([
    {
      label: '显示 / 隐藏',
      click: toggleVisible
    },
    { label: '控制台…', click: () => openConsole() },
    { label: '小游戏…', click: () => openConsole('games') },
    {
      label: fishingState.enabled ? '挂机钓鱼：进行中（点击收竿）' : '挂机钓鱼：未开始（点击开始）',
      click: () => {
        if (!link.sendFishingCmd(fishingState.enabled ? 'stop' : 'start')) sendToPet(IPC.output, localReply('（后端未连接）'))
      }
    },
    {
      label: fishingState.placed ? '钓鱼小窗：收回（隐藏）' : '钓鱼小窗：放到桌宠旁',
      visible: fishingState.enabled,
      click: () => {
        void backendRequest('PUT', '/api/fishing/settings', { hud_placed: !fishingState.placed }).then((r) => {
          if (!r.ok) sendToPet(IPC.output, localReply(`（没改成：${r.error}）`))
        })
      }
    },
    { label: cameraOn
        ? '摄像头：已开启（点击关闭）'
        : cameraEnabled
          ? '摄像头：已启用但暂停中（点击关闭）'
          : '摄像头：已关闭（点击开启）', click: () => void toggleCamera() },
    { label: '重置位置（移回屏幕右下角）', click: resetPosition },
    { label: '关闭全部感知（麦克风 + 屏幕 + 摄像头 + 主动对话）', click: () => void allSensesOff() },
    { type: 'separator' },
    { label: '退出', click: () => app.quit() }
  ])
}

function refreshTray(): void {
  if (!tray) return
  tray.setToolTip(cameraOn ? '桌宠（摄像头开启中）' : cameraEnabled ? '桌宠（摄像头暂停中）' : '桌宠')
  tray.setContextMenu(buildMenu())
}

function createTray(): void {
  tray = new Tray(trayIcon())
  refreshTray()
}

/**
 * Keeps the pet above ordinary windows, but below system surfaces that open later: the taskbar's
 * hidden-icons flyout, the Start menu, notification panels. Those are topmost windows too, and whichever
 * topmost window was raised last is in front, so the pet must not keep raising itself on a timer (it used
 * to, and covered the tray flyout). It is raised again only when the user grabs it (startDrag).
 */
function keepOnTop(w: BrowserWindow): void {
  w.setAlwaysOnTop(true, 'floating')
}

function createWindow(cfg: FrontendConfig): void {
  const pos = initialPosition(cfg)
  baseWindow = { width: cfg.window.width, height: cfg.window.height }
  win = new BrowserWindow({
    ...pos,
    width: cfg.window.width,
    height: cfg.window.height,
    show: false,
    transparent: true,
    frame: false,
    resizable: false,
    maximizable: false,
    fullscreenable: false,
    hasShadow: false,
    skipTaskbar: true,
    alwaysOnTop: cfg.window.alwaysOnTop,
    backgroundColor: '#00000000',
    webPreferences: secureWebPreferences('index')
  })

  if (cfg.window.alwaysOnTop) keepOnTop(win)

  setIgnoreMouse(true, true)
  lockDownNavigation(win.webContents)
  captureRenderer(flog, win.webContents, 'pet')
  win.webContents.on('did-start-loading', resetInteractionState)
  win.webContents.on('render-process-gone', resetInteractionState)
  win.webContents.on('did-finish-load', () => {
    // A reloaded renderer has no cursor yet; resend it so hit-testing starts without a mouse move.
    lastCursor = null
    sendToPet(IPC.linkStatus, link.current)
    sendToPet(IPC.voice, { type: 'mic', listen: micAllowed })
    sendToPet(IPC.voice, { type: 'camera', on: cameraOn, present: cameraPresent, enabled: cameraEnabled })
    sendToPet(IPC.fishing, fishingState)
  })

  if (!app.isPackaged) {
    win.webContents.on('console-message', (e) => console.log(`[renderer:${e.level}] ${e.message}`))
  }

  win.once('ready-to-show', () => win?.showInactive())
  win.on('closed', () => {
    stopDrag()
    win = null
  })

  loadPage(win, 'index')
}

function fromPet(sender: Electron.WebContents): boolean {
  return !!win && sender === win.webContents
}

function registerPetIpc(): void {
  ipcMain.handle(IPC.getConfig, (e) => {
    if (!fromPet(e.sender)) throw new Error('forbidden')
    return rendererConfig
  })
  ipcMain.handle(IPC.getState, (e): PetState => {
    if (!fromPet(e.sender)) throw new Error('forbidden')
    return { link: link.current, micListen: micAllowed, fishing: fishingState }
  })
  ipcMain.on(IPC.fishingHook, (e, seq: unknown, clicked: unknown) => {
    if (fromPet(e.sender) && typeof seq === 'number' && Number.isInteger(seq) && seq >= 0) link.sendFishingHook(seq, clicked === true)
  })
  ipcMain.on(IPC.setIgnoreMouse, (e, ignore: unknown) => {
    if (fromPet(e.sender)) setIgnoreMouse(ignore === true)
  })
  ipcMain.on(IPC.fitWindow, (e, need: unknown) => {
    if (fromPet(e.sender)) fitWindow(need)
  })
  ipcMain.on(IPC.dragStart, (e) => {
    if (fromPet(e.sender)) startDrag()
  })
  ipcMain.on(IPC.dragEnd, (e) => {
    if (fromPet(e.sender)) stopDrag()
  })
  ipcMain.on(IPC.openMenu, (e) => {
    if (fromPet(e.sender) && win) buildMenu().popup({ window: win })
  })
  ipcMain.on(IPC.sendText, (e, raw: unknown) => {
    if (!fromPet(e.sender) || typeof raw !== 'string') return
    const text = raw.trim().slice(0, MAX_CHAT_CHARS)
    if (!text) return
    if (!link.sendEvent('user.text', { text })) {
      sendToPet(IPC.output, localReply('（后端未连接：在 backend 目录运行 uv run python -m api）'))
    }
  })
  ipcMain.on(IPC.sendImage, (e, rawText: unknown, rawImage: unknown) => {
    if (!fromPet(e.sender) || typeof rawText !== 'string' || typeof rawImage !== 'string') return
    const text = rawText.trim().slice(0, MAX_CHAT_CHARS)
    // The renderer downscales to a JPEG well under this; anything bigger is not from our code.
    if (!text || rawImage.length > MAX_IMAGE_DATA_URL_CHARS || !/^data:image\/(?:jpeg|png|webp|gif);base64,/.test(rawImage)) return
    void (async () => {
      const up = await uploadImage(rawImage)
      if (!up.ok) return sendToPet(IPC.output, localReply(`（图片没发出去：${up.error}）`))
      if (!link.sendEvent('user.text', { text, image_id: up.id })) {
        sendToPet(IPC.output, localReply('（后端未连接：在 backend 目录运行 uv run python -m api）'))
      }
    })()
  })
  ipcMain.on(IPC.micFrame, (e, frame: unknown) => {
    if (!fromPet(e.sender) || !micAllowed) return
    const bytes =
      frame instanceof ArrayBuffer ? new Uint8Array(frame) : frame instanceof Uint8Array ? frame : null
    if (bytes && bytes.byteLength > 0 && bytes.byteLength <= MAX_MIC_FRAME_BYTES) link.sendAudio(bytes)
  })
  ipcMain.on(IPC.playback, (e, playing: unknown) => {
    if (fromPet(e.sender)) link.sendPlayback(playing === true)
  })
  ipcMain.on(IPC.micError, (e, msg: unknown) => {
    if (fromPet(e.sender)) console.warn('[pet] 麦克风不可用：', String(msg).slice(0, 300))
  })
  ipcMain.on(IPC.interact, (e, raw: unknown) => {
    if (!fromPet(e.sender) || typeof raw !== 'object' || raw === null) return
    const { kind, area } = raw as { kind?: unknown; area?: unknown }
    if (kind === 'click' && (area === 'head' || area === 'body')) {
      link.sendEvent('pet.interaction', { kind: 'click', area })
    }
  })
}

if (!app.requestSingleInstanceLock()) {
  app.quit()
} else {
  app.whenReady().then(async () => {
    void flog.prune()
    flog.write('INFO', 'main', `桌宠启动（Electron ${process.versions.electron}${app.isPackaged ? '' : '，开发模式'}）`)
    let cfg: FrontendConfig
    try {
      cfg = await loadFrontendConfig(configPath())
    } catch (e) {
      console.error('配置错误', configPath(), e)
      dialog.showErrorBox('配置错误', `${configPath()}\n\n${e instanceof Error ? e.message : String(e)}`)
      app.quit()
      return
    }
    await loadActionsFor(cfg.model.path)
    try {
      petStyle = pickPetStyle(await loadUiConfig(uiConfigPath()))
    } catch (e) {
      // A broken console.yaml must not stop the pet; the console page reports the error.
      console.warn('[pet] 界面配置加载失败，使用默认样式：', e instanceof Error ? e.message : e)
    }
    rendererConfig = toRendererConfig(cfg)

    // Only the pet window may use the microphone (audio only), and only while listening is on.
    const micPermitted = (wc: Electron.WebContents | null): boolean => micAllowed && !!wc && !!win && wc === win.webContents
    session.defaultSession.setPermissionRequestHandler((wc, perm, cb, details) => {
      const media = details as Electron.MediaAccessPermissionRequest
      const audioOnly = Array.isArray(media.mediaTypes) && media.mediaTypes.length > 0 && media.mediaTypes.every((t) => t === 'audio')
      cb(perm === 'media' && audioOnly && micPermitted(wc))
    })
    session.defaultSession.setPermissionCheckHandler(
      (wc, perm, _origin, details) => perm === 'media' && details.mediaType === 'audio' && micPermitted(wc)
    )
    if (app.isPackaged) Menu.setApplicationMenu(null)

    registerPetIpc()
    registerCodeIpc()
    registerConsole({
      configPath: configPath(),
      configDir: configDir(),
      uiConfigPath: uiConfigPath(),
      publicDir: publicDir(),
      stickersPath: stickersPath(),
      stickersDir: stickersDir(),
      onStickersChanged: (list) => {
        stickers = list
        link.updateLabels()
      },
      onSaved: async (next) => {
        const prev = rendererConfig
        if (prev.modelPath !== next.model.path) {
          await loadActionsFor(next.model.path)
          link.updateLabels()
        }
        rendererConfig = toRendererConfig(next)
        baseWindow = { width: next.window.width, height: next.window.height }
        if (prev.modelPath !== rendererConfig.modelPath) win?.webContents.reload()
        else sendLayout(rendererConfig)
      },
      onPreview: sendLayout,
      onPreviewAbandoned: () => sendLayout(rendererConfig),
      onActionsSaved: (map) => {
        actions = map
        rendererConfig = { ...rendererConfig, actions }
        sendToPet(IPC.actions, map)
        link.updateLabels()
      },
      onPlay: (cmd) => sendToPet(IPC.play, cmd),
      onUiSaved: (ui) => {
        petStyle = pickPetStyle(ui)
        rendererConfig = { ...rendererConfig, petStyle }
        sendToPet(IPC.petStyle, petStyle)
      },
      log: flog,
      hotkeys: {
        state: async () => {
          await loadAndApplyHotkeys()
          return hotkeysState()
        },
        save: async (raw) => {
          hotkeys.apply(await saveHotkeys(hotkeysPath(), raw))
          hotkeysError = null
          return hotkeysState()
        },
        suspend: (s) => hotkeys.setSuspended(s)
      }
    })
    createWindow(cfg)
    createTray()
    await loadAndApplyHotkeys()
    startCursorStream(cfg.eyeTracking.fps)
    try {
      stickers = await loadStickers(stickersPath())
    } catch (e) {
      console.warn('[stickers] 读取表情包失败：', e instanceof Error ? e.message : e)
    }
    link.start()
    if (!app.isPackaged && process.env['PET_OPEN_CONSOLE'] === '1') openConsole()
  })

  app.on('before-quit', () => {
    hotkeys.dispose()
    if (cursorTimer) clearInterval(cursorTimer)
    cursorTimer = null
    stopDrag()
    link.stop()
    tray?.destroy()
    tray = null
  })

  app.on('window-all-closed', () => app.quit())
}
