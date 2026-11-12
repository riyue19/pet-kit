import type { ActionMap, AgentOutput, LinkStatus, ModelActions, PetInteraction, PlayCommand, VoiceMessage } from './agent'
import type { BackendMethod, BackendResult, LogQuery, LogsResponse } from './backend'
import type { HotkeysState } from './hotkeys'
import type { Point } from './geometry'
import type { PetStyle, StyleFields, StyleScheme } from './ui-style'
import type { StickerEntry, StickerImportResult, StickerPatch } from './stickers'
import type { FishingMessage, FishingState } from './fishing'

export const IPC = {
  getConfig: 'pet:get-config',
  getState: 'pet:get-state',
  setIgnoreMouse: 'pet:set-ignore-mouse',
  dragStart: 'pet:drag-start',
  dragEnd: 'pet:drag-end',
  openMenu: 'pet:open-menu',
  cursor: 'pet:cursor',
  layout: 'pet:layout',
  fitWindow: 'pet:fit-window',
  sendText: 'pet:send-text',
  sendImage: 'pet:send-image',
  interact: 'pet:interact',
  output: 'pet:output',
  linkStatus: 'pet:link-status',
  actions: 'pet:actions',
  petStyle: 'pet:style',
  play: 'pet:play',
  voice: 'pet:voice',
  audio: 'pet:audio',
  micFrame: 'pet:mic-frame',
  playback: 'pet:playback',
  micError: 'pet:mic-error',
  fishing: 'pet:fishing',
  fishingHook: 'pet:fishing-hook',
  consoleModelState: 'console:model-state',
  consoleImportModel: 'console:import-model',
  consoleSaveModel: 'console:save-model',
  consolePreviewModel: 'console:preview-model',
  consoleGetUi: 'console:get-ui',
  consoleSaveUi: 'console:save-ui',
  consoleBackend: 'console:backend',
  consoleModelActions: 'console:model-actions',
  consoleSaveModelActions: 'console:save-model-actions',
  consolePlay: 'console:play',
  consoleStickers: 'console:stickers',
  consoleImportStickers: 'console:import-stickers',
  consoleUpdateSticker: 'console:update-sticker',
  consoleDeleteSticker: 'console:delete-sticker',
  consoleLogs: 'console:logs',
  consoleOpenLogs: 'console:open-logs',
  consoleShowFile: 'console:show-file',
  consoleHotkeys: 'console:hotkeys',
  consoleSaveHotkeys: 'console:save-hotkeys',
  consoleSuspendHotkeys: 'console:suspend-hotkeys',
  consoleNavigate: 'console:navigate',
  consoleInitialPage: 'console:initial-page',
  codeGet: 'code:get',
  codeShow: 'code:show',
  codeCopy: 'code:copy'
} as const

export type Framing = 'full' | 'half'

/** Model placement that can change without reloading the model. */
export interface ModelLayout {
  scale: number | 'auto'
  framing: Framing
  halfRatio: number
}

export interface RendererConfig extends ModelLayout {
  modelPath: string
  alphaThreshold: number
  actions: ActionMap
  petStyle: PetStyle
}

/** Link + microphone state, pulled by the renderer once its listeners exist (pushes before that are lost). */
export interface PetState {
  link: LinkStatus
  micListen: boolean
  fishing: FishingState
}

export interface PetBridge {
  getConfig(): Promise<RendererConfig>
  getState(): Promise<PetState>
  setIgnoreMouse(ignore: boolean): void
  dragStart(): void
  dragEnd(): void
  openMenu(): void
  /** Cursor position in window client coordinates (DIP), may lie outside the window. */
  onCursor(cb: (p: Point) => void): () => void
  onLayout(cb: (layout: ModelLayout) => void): () => void
  /** Smallest window size the current model layout needs (grows past the configured size, never below it); null = configured size. */
  fitWindow(size: { width: number; height: number } | null): void
  sendText(text: string): void
  /** Text plus one picture (JPEG/PNG/WebP data URL); the main process parks the picture at the backend first. */
  sendImage(text: string, dataUrl: string): void
  interact(i: PetInteraction): void
  onOutput(cb: (o: AgentOutput) => void): () => void
  onLinkStatus(cb: (s: LinkStatus) => void): () => void
  onActions(cb: (map: ActionMap) => void): () => void
  /** Input box / bubble style changed in the console. */
  onPetStyle(cb: (style: PetStyle) => void): () => void
  onPlay(cb: (cmd: PlayCommand) => void): () => void
  onVoice(cb: (m: VoiceMessage) => void): () => void
  /** TTS PCM chunk belonging to the latest audio_start. */
  onAudio(cb: (chunk: Uint8Array) => void): () => void
  /** One microphone frame: 16 kHz mono 16-bit LE PCM. */
  sendMic(frame: ArrayBuffer): void
  /** Reports whether TTS audio is audible right now (echo guard / barge-in threshold). */
  setPlayback(playing: boolean): void
  /** The microphone could not be opened (permission denied, no device, ...). */
  micError(message: string): void
  /** Fishing HUD state / animation timelines (already decided by the backend). */
  onFishing(cb: (m: FishingMessage) => void): () => void
  /** Answer to a bite window: did the user click in time. */
  fishingHook(seq: number, clicked: boolean): void
}

export interface ModelEntry {
  name: string
  path: string
}

export interface ModelSettings extends ModelLayout {
  path: string
}

export interface ModelState {
  models: ModelEntry[]
  current: ModelSettings
}

export interface UiSettings extends StyleFields {
  theme: 'auto' | 'light' | 'dark'
  accent: string
  fontSize: number
  /** Saved style schemes (preset choices + custom CSS), switchable from the console. */
  styles: StyleScheme[]
  /** Order of the console's left menu (page keys); pages not listed follow in the built-in order. */
  navOrder: string[]
}

/** A piece of code the pet wants to show (backend show_code); text only, rendered with textContent. */
export interface CodePayload {
  title: string
  language: string
  code: string
}

export interface CodeBridge {
  /** The code currently shown (pulled once at startup; a push before the listener exists would be lost). */
  get(): Promise<CodePayload | null>
  onShow(cb: (c: CodePayload) => void): () => void
  /** Copies the code the main process holds (never renderer-supplied text) to the clipboard. */
  copy(): Promise<boolean>
}

export interface ConsoleBridge {
  modelState(): Promise<ModelState>
  /** Opens a file dialog; resolves null if the user cancels. */
  importModel(): Promise<ModelEntry | null>
  saveModel(model: ModelSettings): Promise<void>
  /** Applies placement to the pet immediately; reverted when the console closes unsaved. */
  previewModel(layout: ModelLayout): void
  getUi(): Promise<UiSettings>
  saveUi(ui: UiSettings): Promise<UiSettings>
  /** Calls the backend through the main process, which holds the auth token. */
  backend<T = unknown>(method: BackendMethod, path: string, body?: unknown): Promise<BackendResult<T>>
  /** Inventory + label mapping of the currently saved model. */
  modelActions(): Promise<ModelActions>
  /** Validates and writes config/models/<model>.yaml; the pet picks it up immediately. */
  saveModelActions(map: ActionMap): Promise<ActionMap>
  /** Plays a raw expression/motion on the pet for preview. */
  playAction(cmd: PlayCommand): void
  /** Stickers in config/stickers.yaml, with previews. */
  stickers(): Promise<StickerEntry[]>
  /** Opens a file dialog (several pictures at once); resolves the new full list plus the files that were skipped. */
  importStickers(): Promise<StickerImportResult>
  /** Renames / enables / disables one sticker, saved at once; rejects with a readable message (e.g. duplicate name). */
  updateSticker(id: string, patch: StickerPatch): Promise<StickerEntry[]>
  /** Removes the sticker and its picture file. */
  deleteSticker(id: string): Promise<StickerEntry[]>
  /** Frontend (Electron main / pet window / console window) log entries; same shape as GET /api/logs. */
  frontendLogs(query: LogQuery): Promise<LogsResponse>
  /** Opens the log folder in Explorer; resolves to an error message, or '' on success. */
  openLogFolder(): Promise<string>
  /** Reveals an existing .db/.jsonl file in Explorer (e.g. MemoryState.stats.db_path); '' on success, else an error. */
  showFileInFolder(path: string): Promise<string>
  /** config/hotkeys.yaml plus whether each combo is registered with the OS. */
  hotkeys(): Promise<HotkeysState>
  /** Validates + writes config/hotkeys.yaml and re-registers at once; rejects with a readable message. */
  saveHotkeys(hotkeys: unknown): Promise<HotkeysState>
  /** true while recording a combo in the console so existing hotkeys don't fire; always reset on close. */
  suspendHotkeys(suspended: boolean): void
  /** The main process asks the open console to show a page (e.g. 小游戏 after accepting the pet's invitation). */
  onNavigate(cb: (page: string) => void): void
  /** The page the console was opened for, if any (read once on start). */
  initialPage(): Promise<string | null>
}
