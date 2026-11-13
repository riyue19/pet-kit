/** Edges in physical pixels: left, top, right, bottom. */
export interface Edges {
  left: number
  top: number
  right: number
  bottom: number
}

export interface ForegroundInfo {
  className: string
  pid: number
  iconic: boolean
  /** Visible frame of the foreground window. */
  frame: Edges
  /** Full bounds (not the work area) of the monitor the foreground window is on. */
  monitor: Edges
  /** The foreground window is on the same monitor as the pet. */
  samePetMonitor: boolean
}

/**
 * Desktop, taskbar, tray overflow flyout, Start menu, Task View, notification panels and the like.
 * They are never "a fullscreen app", and while one of them is in front the pet must stay below it.
 */
export const SHELL_CLASSES: ReadonlySet<string> = new Set([
  'Progman',
  'WorkerW',
  'Shell_TrayWnd',
  'Shell_SecondaryTrayWnd',
  'NotifyIconOverflowWindow',
  'TopLevelWindowForOverflowXamlIsland',
  'Windows.UI.Core.CoreWindow',
  'XamlExplorerHostIslandWindow',
  'MultitaskingViewFrame',
  'ForegroundStaging',
  'LockScreenBackstopFrame'
])

export type TopMode = 'raise' | 'normal' | 'keep'

/**
 * 'raise' when the foreground window is another program covering the whole monitor the pet is on
 * (fullscreen video, borderless/fullscreen game, slideshow); 'keep' when the pet itself is in front
 * (the user is typing into it, so don't drop it under the game); otherwise 'normal'.
 */
export function topModeFor(fg: ForegroundInfo | null, ownPid: number): TopMode {
  if (!fg) return 'normal'
  if (fg.pid === ownPid) return 'keep'
  if (fg.iconic || SHELL_CLASSES.has(fg.className) || !fg.samePetMonitor) return 'normal'
  const f = fg.frame
  const m = fg.monitor
  const covers = f.left <= m.left && f.top <= m.top && f.right >= m.right && f.bottom >= m.bottom
  return covers ? 'raise' : 'normal'
}
