import { join } from 'node:path'
import type { BrowserWindow, WebContents } from 'electron'

export function secureWebPreferences(preload: string): Electron.WebPreferences {
  return {
    preload: join(__dirname, `../preload/${preload}.js`),
    contextIsolation: true,
    nodeIntegration: false,
    sandbox: true
  }
}

export function lockDownNavigation(contents: WebContents): void {
  contents.setWindowOpenHandler(() => ({ action: 'deny' }))
  contents.on('will-navigate', (e) => e.preventDefault())
}

export function loadPage(win: BrowserWindow, page: 'index' | 'console' | 'code'): void {
  const devUrl = process.env['ELECTRON_RENDERER_URL']
  if (devUrl) win.loadURL(`${devUrl}/${page}.html`)
  else win.loadFile(join(__dirname, `../renderer/${page}.html`))
}
