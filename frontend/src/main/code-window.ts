import { BrowserWindow, clipboard, ipcMain, type IpcMainInvokeEvent } from 'electron'
import { IPC, type CodePayload } from '../shared/ipc'
import { captureRenderer, type FrontendLog } from './logger'
import { loadPage, lockDownNavigation, secureWebPreferences } from './windows'

let codeWin: BrowserWindow | null = null
// Kept in the main process so "copy" never trusts text coming from the renderer.
let current: CodePayload | null = null

function assertFromCode(e: IpcMainInvokeEvent): void {
  if (!codeWin || e.sender !== codeWin.webContents) throw new Error('forbidden')
}

export function registerCodeIpc(): void {
  ipcMain.handle(IPC.codeGet, (e) => {
    assertFromCode(e)
    return current
  })
  ipcMain.handle(IPC.codeCopy, (e): boolean => {
    assertFromCode(e)
    if (!current) return false
    clipboard.writeText(current.code)
    return true
  })
}

/** Shows (or updates) the code window without taking focus from whatever the user is typing in. */
export function showCode(payload: CodePayload, log?: FrontendLog): void {
  current = payload
  if (codeWin && !codeWin.isDestroyed()) {
    codeWin.webContents.send(IPC.codeShow, payload)
    if (codeWin.isMinimized()) codeWin.restore()
    codeWin.showInactive()
    codeWin.moveTop()
    return
  }
  codeWin = new BrowserWindow({
    width: 620,
    height: 480,
    minWidth: 360,
    minHeight: 240,
    autoHideMenuBar: true,
    title: '代码',
    show: false,
    backgroundColor: '#1e1e24',
    // A browser or terminal opened later must not bury the code the user just asked for.
    alwaysOnTop: true,
    webPreferences: secureWebPreferences('code')
  })
  codeWin.setMenu(null)
  lockDownNavigation(codeWin.webContents)
  if (log) captureRenderer(log, codeWin.webContents, 'code')
  codeWin.once('ready-to-show', () => codeWin?.showInactive())
  codeWin.on('closed', () => {
    codeWin = null
  })
  loadPage(codeWin, 'code')
}
