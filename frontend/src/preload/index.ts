import { contextBridge, ipcRenderer, type IpcRendererEvent } from 'electron'
import { IPC, type PetBridge } from '../shared/ipc'

function subscribe<T>(channel: string, cb: (v: T) => void): () => void {
  const listener = (_e: IpcRendererEvent, v: T): void => cb(v)
  ipcRenderer.on(channel, listener)
  return () => ipcRenderer.removeListener(channel, listener)
}

const bridge: PetBridge = {
  getConfig: () => ipcRenderer.invoke(IPC.getConfig),
  getState: () => ipcRenderer.invoke(IPC.getState),
  setIgnoreMouse: (ignore) => ipcRenderer.send(IPC.setIgnoreMouse, ignore === true),
  dragStart: () => ipcRenderer.send(IPC.dragStart),
  dragEnd: () => ipcRenderer.send(IPC.dragEnd),
  openMenu: () => ipcRenderer.send(IPC.openMenu),
  onCursor: (cb) => subscribe(IPC.cursor, cb),
  onLayout: (cb) => subscribe(IPC.layout, cb),
  fitWindow: (size) => ipcRenderer.send(IPC.fitWindow, size),
  sendText: (text) => ipcRenderer.send(IPC.sendText, text),
  sendImage: (text, dataUrl) => ipcRenderer.send(IPC.sendImage, text, dataUrl),
  interact: (i) => ipcRenderer.send(IPC.interact, i),
  onOutput: (cb) => subscribe(IPC.output, cb),
  onLinkStatus: (cb) => subscribe(IPC.linkStatus, cb),
  onActions: (cb) => subscribe(IPC.actions, cb),
  onPetStyle: (cb) => subscribe(IPC.petStyle, cb),
  onPlay: (cb) => subscribe(IPC.play, cb),
  onVoice: (cb) => subscribe(IPC.voice, cb),
  onAudio: (cb) => subscribe(IPC.audio, cb),
  sendMic: (frame) => ipcRenderer.send(IPC.micFrame, frame),
  setPlayback: (playing) => ipcRenderer.send(IPC.playback, playing === true),
  micError: (message) => ipcRenderer.send(IPC.micError, String(message)),
  onFishing: (cb) => subscribe(IPC.fishing, cb),
  fishingHook: (seq, clicked) => ipcRenderer.send(IPC.fishingHook, seq, clicked === true)
}

contextBridge.exposeInMainWorld('pet', bridge)
