/** Small helpers shared by the console's pages and stores. No DOM, no Vue. */

/** A plain deep copy. Also what Electron IPC needs: it cannot clone Vue's reactive proxies. */
export const clone = <T>(v: T): T => JSON.parse(JSON.stringify(v)) as T

export const pad2 = (n: number): string => String(n).padStart(2, '0')

/** "14:05:09" for a timestamp in seconds. */
export function clockTime(tsSeconds: number): string {
  const d = new Date(tsSeconds * 1000)
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`
}
