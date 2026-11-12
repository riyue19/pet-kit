import { contextBridge, ipcRenderer } from 'electron'
import { IPC, type ConsoleBridge } from '../shared/ipc'

const bridge: ConsoleBridge = {
  modelState: () => ipcRenderer.invoke(IPC.consoleModelState),
  importModel: () => ipcRenderer.invoke(IPC.consoleImportModel),
  saveModel: (model) => ipcRenderer.invoke(IPC.consoleSaveModel, model),
  previewModel: (layout) => ipcRenderer.send(IPC.consolePreviewModel, layout),
  getUi: () => ipcRenderer.invoke(IPC.consoleGetUi),
  saveUi: (ui) => ipcRenderer.invoke(IPC.consoleSaveUi, ui),
  backend: (method, path, body) => ipcRenderer.invoke(IPC.consoleBackend, method, path, body),
  modelActions: () => ipcRenderer.invoke(IPC.consoleModelActions),
  saveModelActions: (map) => ipcRenderer.invoke(IPC.consoleSaveModelActions, map),
  playAction: (cmd) => ipcRenderer.send(IPC.consolePlay, cmd),
  stickers: () => ipcRenderer.invoke(IPC.consoleStickers),
  importStickers: () => ipcRenderer.invoke(IPC.consoleImportStickers),
  updateSticker: (id, patch) => ipcRenderer.invoke(IPC.consoleUpdateSticker, id, patch),
  deleteSticker: (id) => ipcRenderer.invoke(IPC.consoleDeleteSticker, id),
  frontendLogs: (query) => ipcRenderer.invoke(IPC.consoleLogs, query),
  openLogFolder: () => ipcRenderer.invoke(IPC.consoleOpenLogs),
  showFileInFolder: (path) => ipcRenderer.invoke(IPC.consoleShowFile, path),
  hotkeys: () => ipcRenderer.invoke(IPC.consoleHotkeys),
  saveHotkeys: (hotkeys) => ipcRenderer.invoke(IPC.consoleSaveHotkeys, hotkeys),
  suspendHotkeys: (suspended) => ipcRenderer.send(IPC.consoleSuspendHotkeys, suspended === true),
  onNavigate: (cb) => {
    ipcRenderer.on(IPC.consoleNavigate, (_e, page: unknown) => {
      if (typeof page === 'string') cb(page)
    })
  },
  initialPage: () => ipcRenderer.invoke(IPC.consoleInitialPage)
}

contextBridge.exposeInMainWorld('petConsole', bridge)
