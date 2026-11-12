/**
 * The fishing animation inside the console (小游戏 → 挂机钓鱼): the same HUD as next to the pet, large.
 * The element and its HUD are created once and kept for as long as the console is open, so leaving the page
 * (or going back to the lobby) and returning does not restart anything; what it shows comes from the backend
 * (GET /api/fishing/hud, polled while the scene is on screen).
 */
import { reactive } from 'vue'
import { createFishingHud, type FishingHud } from '../fishing-hud'
import { toFishingMessage } from '../../shared/fishing'
import { loadFishing } from './fishing'

const POLL_MS = 1000

interface HudPoll {
  state: Record<string, unknown>
  pending_seq: number | null
  feed: { n: number; msg: Record<string, unknown> }[]
  last: number
  now: number
}

export const scene = reactive({
  enabled: false,
  placed: true,
  scale: 1,
  /** Unix seconds (backend clock) when the line in the water was cast; null = no line out. */
  castTs: null as number | null,
  /** Local clock minus backend clock, seconds. */
  skew: 0,
  /** A bite window is open here: clicking the picture lands the fish. */
  biting: false,
  loaded: false,
  error: null as string | null
})

let el: HTMLDivElement | null = null
let hud: FishingHud | null = null
// Number of the last HUD timeline played; null = ask only for the current number (skip what was missed).
let cursor: number | null = null
let timer: number | undefined
let polling = false

/** The scene's element (created on first use); the page puts it into its layout. */
export function sceneElement(): HTMLDivElement {
  if (el) return el
  el = document.createElement('div')
  const node = el
  hud = createFishingHud(
    node,
    (seq) => {
      void window.petConsole.backend('POST', '/api/fishing/hook', { seq, clicked: true }).then(() => loadFishing())
    },
    () => {
      scene.biting = hud?.clickable() ?? false
    },
    { embed: true }
  )
  return node
}

async function poll(): Promise<void> {
  if (polling || !hud) return
  polling = true
  try {
    const path = cursor === null ? '/api/fishing/hud' : `/api/fishing/hud?after=${cursor}`
    const res = await window.petConsole.backend<HudPoll>('GET', path)
    if (!res.ok) {
      scene.error = res.error
      return
    }
    scene.error = null
    const d = res.data
    const state = toFishingMessage(d.state)
    if (state && state.type === 'fishing.state') {
      hud.handle(state)
      scene.enabled = state.enabled
      scene.placed = state.placed
      scene.scale = state.scale
      scene.castTs = state.castTs
    }
    scene.skew = Date.now() / 1000 - d.now
    scene.loaded = true
    let settled = false
    for (const item of d.feed ?? []) {
      const msg = toFishingMessage(item.msg)
      if (!msg) continue
      hud.handle(msg)
      if (msg.type === 'fishing.anim' && msg.phase !== 'until_bite') settled = true
    }
    cursor = d.last
    // Bag, coins and the codex changed with it.
    if (settled) void loadFishing()
  } catch (e) {
    scene.error = e instanceof Error ? e.message : String(e)
  } finally {
    polling = false
  }
}

export function startScene(): void {
  sceneElement()
  window.clearInterval(timer)
  cursor = null
  void poll()
  timer = window.setInterval(() => void poll(), POLL_MS)
}

export function stopScene(): void {
  window.clearInterval(timer)
  timer = undefined
}

/** Right after a command changed something: don't wait for the next poll. */
export function refreshScene(): void {
  void poll()
}
