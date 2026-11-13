import type { ForegroundInfo, Edges } from '../shared/fullscreen'

/** Reads the foreground window via user32/dwmapi (koffi). Windows only; null when unavailable. */
export interface ForegroundReader {
  read(petHwnd: number): ForegroundInfo | null
}

const DWMWA_EXTENDED_FRAME_BOUNDS = 9
const MONITOR_DEFAULTTONEAREST = 2

export function createForegroundReader(): ForegroundReader | null {
  if (process.platform !== 'win32') return null
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const koffi = require('koffi') as typeof import('koffi')
  const user32 = koffi.load('user32.dll')
  const dwmapi = koffi.load('dwmapi.dll')
  const RECT = koffi.struct('PET_RECT', { left: 'int32', top: 'int32', right: 'int32', bottom: 'int32' })
  const MONITORINFO = koffi.struct('PET_MONITORINFO', {
    cbSize: 'uint32',
    rcMonitor: RECT,
    rcWork: RECT,
    dwFlags: 'uint32'
  })
  // HWND / HMONITOR as intptr so they come back as plain numbers that can be compared.
  const GetForegroundWindow = user32.func('__stdcall', 'GetForegroundWindow', 'intptr', [])
  const GetClassNameW = user32.func('__stdcall', 'GetClassNameW', 'int', ['intptr', 'void *', 'int'])
  const GetWindowThreadProcessId = user32.func('__stdcall', 'GetWindowThreadProcessId', 'uint32', [
    'intptr',
    koffi.out(koffi.pointer('uint32'))
  ])
  const IsIconic = user32.func('__stdcall', 'IsIconic', 'int', ['intptr'])
  const GetWindowRect = user32.func('__stdcall', 'GetWindowRect', 'int', ['intptr', koffi.out(koffi.pointer(RECT))])
  const MonitorFromWindow = user32.func('__stdcall', 'MonitorFromWindow', 'intptr', ['intptr', 'uint32'])
  const GetMonitorInfoW = user32.func('__stdcall', 'GetMonitorInfoW', 'int', [
    'intptr',
    koffi.inout(koffi.pointer(MONITORINFO))
  ])
  const DwmGetWindowAttribute = dwmapi.func('__stdcall', 'DwmGetWindowAttribute', 'int32', [
    'intptr',
    'uint32',
    koffi.out(koffi.pointer(RECT)),
    'uint32'
  ])
  const rectSize = koffi.sizeof(RECT)
  const monSize = koffi.sizeof(MONITORINFO)

  function frame(hwnd: number): Edges | null {
    const r: Edges = { left: 0, top: 0, right: 0, bottom: 0 }
    if (DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, r, rectSize) === 0) return r
    return GetWindowRect(hwnd, r) ? r : null
  }

  return {
    read(petHwnd: number): ForegroundInfo | null {
      const hwnd = Number(GetForegroundWindow())
      if (!hwnd) return null
      const nameBuf = Buffer.alloc(512)
      const len = GetClassNameW(hwnd, nameBuf, 256)
      const className = nameBuf.toString('utf16le', 0, Math.max(0, len) * 2)
      const pid = [0]
      GetWindowThreadProcessId(hwnd, pid)
      const f = frame(hwnd)
      if (!f) return null
      const mon = Number(MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST))
      const info = { cbSize: monSize, rcMonitor: {}, rcWork: {}, dwFlags: 0 } as {
        cbSize: number
        rcMonitor: Edges
        rcWork: Edges
        dwFlags: number
      }
      if (!mon || !GetMonitorInfoW(mon, info)) return null
      const petMon = Number(MonitorFromWindow(petHwnd, MONITOR_DEFAULTTONEAREST))
      return {
        className,
        pid: pid[0] ?? 0,
        iconic: !!IsIconic(hwnd),
        frame: f,
        monitor: info.rcMonitor,
        samePetMonitor: mon === petMon
      }
    }
  }
}
