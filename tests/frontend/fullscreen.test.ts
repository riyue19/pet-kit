import { describe, expect, it } from 'vitest'
import { topModeFor, type ForegroundInfo } from '../../frontend/src/shared/fullscreen'

const MON = { left: 0, top: 0, right: 1920, bottom: 1080 }
const OWN = 1000

function fg(over: Partial<ForegroundInfo> = {}): ForegroundInfo {
  return { className: 'Chrome_WidgetWin_1', pid: 42, iconic: false, frame: { ...MON }, monitor: MON, samePetMonitor: true, ...over }
}

describe('topModeFor', () => {
  it('raises over a window covering the whole monitor', () => {
    expect(topModeFor(fg(), OWN)).toBe('raise')
    expect(topModeFor(fg({ frame: { left: -8, top: -8, right: 1928, bottom: 1088 } }), OWN)).toBe('raise')
  })

  it('stays normal for a maximized window (taskbar still visible)', () => {
    expect(topModeFor(fg({ frame: { left: 0, top: 0, right: 1920, bottom: 1032 } }), OWN)).toBe('normal')
  })

  it('stays normal for shell surfaces such as the tray flyout and Start menu', () => {
    for (const c of ['Shell_TrayWnd', 'NotifyIconOverflowWindow', 'TopLevelWindowForOverflowXamlIsland', 'Windows.UI.Core.CoreWindow', 'WorkerW']) {
      expect(topModeFor(fg({ className: c }), OWN)).toBe('normal')
    }
  })

  it('ignores fullscreen windows on another monitor, minimized windows and no foreground', () => {
    expect(topModeFor(fg({ samePetMonitor: false }), OWN)).toBe('normal')
    expect(topModeFor(fg({ iconic: true }), OWN)).toBe('normal')
    expect(topModeFor(null, OWN)).toBe('normal')
  })

  it('keeps the current state while the pet itself is in front', () => {
    expect(topModeFor(fg({ pid: OWN }), OWN)).toBe('keep')
  })
})
