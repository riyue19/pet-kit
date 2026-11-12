import { contextBridge, ipcRenderer, type IpcRendererEvent } from 'electron'
import { IPC, type CodeBridge, type CodePayload } from '../shared/ipc'

const bridge: CodeBridge = {
  get: () => ipcRenderer.invoke(IPC.codeGet),
  onShow: (cb) => {
    const listener = (_e: IpcRendererEvent, c: CodePayload): void => cb(c)
    ipcRenderer.on(IPC.codeShow, listener)
    return () => ipcRenderer.removeListener(IPC.codeShow, listener)
  },
  copy: () => ipcRenderer.invoke(IPC.codeCopy)
}

contextBridge.exposeInMainWorld('petCode', bridge)
