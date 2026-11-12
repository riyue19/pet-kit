/**
 * The fishing HUD next to the pet's head: a float on the water, the bite alert, the result card.
 * It only plays back timelines the backend already decided (fishing.anim); the single thing it reports is
 * whether the user clicked during a bite window.
 *
 * Cheap by design: CSS transform / opacity animations only, no SVG filters, a handful of elements.
 * Animations stop while the window is hidden, in the light tier (`lite`), and under prefers-reduced-motion.
 */
import { composeFish, composeJunk } from '../shared/fish/composer'
import type { FishingAnim, FishingCard, FishingMessage, FishingStep } from '../shared/fishing'

const MAX_QUEUE = 3
const SVG_NS = 'http://www.w3.org/2000/svg'

const SCENE = `
<svg class="fh-scene" viewBox="0 0 160 80" aria-hidden="true">
  <rect class="fh-panel" x="1" y="1" width="158" height="78" rx="9"/>
  <circle class="fh-orb" cx="141" cy="16" r="6"/>
  <g class="fh-stars"><circle cx="40" cy="12" r="1"/><circle cx="72" cy="21" r="0.8"/><circle cx="104" cy="10" r="1"/></g>
  <path class="fh-cloud" d="M50,22 q2,-7 9,-4 q4,-6 11,-1 q8,-1 6,5 z"/>
  <path class="fh-rain" d="M30,8 l-3,8 M54,14 l-3,8 M78,6 l-3,8 M102,14 l-3,8 M126,8 l-3,8 M146,16 l-3,8"/>
  <ellipse class="fh-shadow" cx="60" cy="64" rx="8" ry="2.4"/>
  <g class="fh-bubbles"><circle cx="97" cy="60" r="1.2"/><circle cx="87" cy="64" r="0.9"/></g>
  <path class="fh-rod" d="M5,15 L17,7"/>
  <path class="fh-water" d="M8,53 q8,-4 16,0 t16,0 t16,0 t16,0 t16,0 t16,0 t16,0 t16,0 t16,0"/>
  <path class="fh-water fh-water2" d="M14,61 q8,-3 16,0 t16,0 t16,0 t16,0 t16,0 t16,0 t16,0 t16,0"/>
  <ellipse class="fh-ripple fh-r1" cx="92" cy="53" rx="4" ry="1.2"/>
  <ellipse class="fh-ripple fh-r2" cx="92" cy="53" rx="4" ry="1.2"/>
  <g class="fh-splash"><path d="M86,50 l-4,-7 M92,48 v-9 M98,50 l4,-7"/></g>
  <path class="fh-line" d="M16,8 Q60,12 92,40" pathLength="100"/>
  <g class="fh-float"><path d="M92,39 V46"/><ellipse cx="92" cy="50" rx="3.4" ry="4.2"/><path class="fh-band" d="M88.8,49.5 h6.4"/></g>
  <g class="fh-bang"><circle cx="113" cy="27" r="9"/><text x="113" y="32" text-anchor="middle">!</text></g>
  <rect class="fh-winbar" x="8" y="71" width="144" height="3" rx="1.5"/>
</svg>`

export interface FishingHud {
  handle(msg: FishingMessage): void
  /** True while a click on the HUD means something (the bite window): the window must take the mouse then. */
  clickable(): boolean
  /** Shown at all (fishing on, or a card still playing). */
  visible(): boolean
}

function div(cls: string, text = ''): HTMLDivElement {
  const d = document.createElement('div')
  d.className = cls
  d.textContent = text
  return d
}

function picture(svg: string): HTMLDivElement {
  const d = div('fh-pic')
  // Built locally from fixed path data (shared/fish); names are escaped by the composer.
  d.innerHTML = svg
  return d
}

function buildCard(card: FishingCard): HTMLElement[] {
  const text = div('fh-txt')
  switch (card.kind) {
    case 'fish': {
      const tags = [card.rarityLabel, card.first ? '新图鉴' : '', card.trophy ? '巨物' : '', card.record ? '新纪录' : ''].filter(Boolean)
      text.append(div('fh-name', card.name), div('fh-size', `${card.sizeCm} cm`), div('fh-tags', tags.join(' · ')))
      return [picture(composeFish({ template: card.template, patterns: card.patterns, mode: 'icon', label: card.name })), text]
    }
    case 'junk':
      text.append(div('fh-name', card.name), div('fh-tags', '不是鱼'))
      return [picture(composeJunk(card.junkId, { label: card.name })), text]
    case 'bottle':
      text.append(div('fh-letter', card.body || '瓶子里的纸条被水泡花了。'), div('fh-tags', `${card.sender ? `—— ${card.sender}　` : ''}+${card.coins} 金币`))
      return [text]
    case 'digest': {
      text.append(div('fh-name', `钓了 ${card.n} 竿`), div('fh-size', `收获 ${card.fishCount} 条${card.bottles ? ` · 漂流瓶 ${card.bottles}` : ''}`))
      if (card.highlights.length) text.append(div('fh-tags', `${card.highlights.join('、')}${card.more ? ` 等` : ''}`))
      return [text]
    }
  }
}

/** Width of the small window at scale 1 (CSS px); its height follows (2:1). */
export const HUD_BASE_WIDTH = 132
const BASE_FONT_PX = 11

/**
 * `embed`: the HUD fills its container and ignores "placed" and "scale" (the console's fishing scene);
 * otherwise it is the small window next to the pet. `onHook(seq, clicked)`: the bite window ended here.
 */
export function createFishingHud(
  root: HTMLElement,
  onHook: (seq: number, clicked: boolean) => void,
  onChange: () => void,
  opts: { embed?: boolean } = {}
): FishingHud {
  const embed = opts.embed === true
  root.classList.add('fishing-hud')
  root.classList.toggle('fh-embed', embed)
  root.innerHTML = SCENE
  const cardEl = div('fh-card')
  // A few sparks for rare catches: plain elements animated with transform/opacity.
  const sparks = document.createElementNS(SVG_NS, 'svg')
  sparks.setAttribute('class', 'fh-sparks')
  sparks.setAttribute('viewBox', '0 0 160 80')
  for (const [x, y] of [[18, 16], [140, 14], [150, 58], [12, 62], [80, 8], [118, 70]]) {
    const p = document.createElementNS(SVG_NS, 'path')
    p.setAttribute('d', `M${x},${y - 4} l1.2,2.8 2.8,1.2 -2.8,1.2 -1.2,2.8 -1.2,-2.8 -2.8,-1.2 2.8,-1.2z`)
    sparks.append(p)
  }
  root.append(cardEl, sparks)

  let enabled = false
  let placed = true
  let lastSeq = -1
  let playing = false
  // Ends the open bite window: true = clicked, false = time ran out, null = answered elsewhere (don't report).
  let biteResolve: ((clicked: boolean | null) => void) | null = null
  const queue: FishingAnim[] = []
  const reduced = matchMedia('(prefers-reduced-motion: reduce)')

  const setState = (s: string): void => {
    root.dataset.state = s
  }
  const shown = (): boolean => embed || (placed && (enabled || playing))
  const rest = (): void => {
    // idle = the line is in the water, waiting (for real: minutes to hours).
    setState(enabled ? 'idle' : 'off')
    root.classList.toggle('on', shown())
    delete root.dataset.rarity
    onChange()
  }
  const setLite = (lite: boolean): void => {
    root.classList.toggle('lite', lite || reduced.matches)
  }
  const sleep = (ms: number): Promise<void> => new Promise((r) => window.setTimeout(r, ms))

  async function playStep(anim: FishingAnim, s: FishingStep): Promise<void> {
    if (s.card) {
      cardEl.replaceChildren(...buildCard(s.card))
      cardEl.dataset.kind = s.card.kind
      if (s.card.kind === 'fish') root.dataset.rarity = s.card.rarity
    }
    root.classList.toggle('minimal', s.minimal)
    if (s.window) root.style.setProperty('--fh-win', `${s.dur}ms`)
    setState(s.state)
    if (!s.window) return sleep(s.dur)
    onChange()
    const clicked = await new Promise<boolean | null>((resolve) => {
      biteResolve = resolve
      window.setTimeout(() => resolve(false), s.dur)
    })
    biteResolve = null
    // Only a click is reported; when nobody clicks, the backend's own timer ends the window (so a second
    // HUD showing the same window a moment later still gets its full time).
    if (clicked) onHook(anim.seq, true)
    // Until the backend says how it ended (it answers within moments).
    setState('wait')
    onChange()
  }

  async function drain(): Promise<void> {
    if (playing) return
    playing = true
    try {
      for (let anim = queue.shift(); anim; anim = queue.shift()) {
        root.classList.toggle('on', shown())
        setLite(anim.lite)
        onChange()
        for (const s of anim.steps) await playStep(anim, s)
        // A bite window ends in "wait": the resolve timeline takes over from there.
        if (anim.phase !== 'until_bite') rest()
      }
    } finally {
      playing = false
      if (root.dataset.state !== 'wait') rest()
    }
  }

  // Embedded: the card's text grows with the container (the drawing is an SVG and scales by itself).
  if (embed && typeof ResizeObserver !== 'undefined') {
    new ResizeObserver(() => {
      const w = root.clientWidth
      if (w > 0) root.style.fontSize = `${((w / HUD_BASE_WIDTH) * BASE_FONT_PX).toFixed(1)}px`
    }).observe(root)
  }
  root.addEventListener('click', () => biteResolve?.(true))
  document.addEventListener('visibilitychange', () => root.classList.toggle('paused', document.hidden))
  reduced.addEventListener('change', () => setLite(root.classList.contains('lite')))
  setLite(false)
  rest()

  return {
    handle(msg) {
      if (msg.type === 'fishing.state') {
        enabled = msg.enabled
        placed = msg.placed
        root.dataset.sky = msg.sky
        root.dataset.weather = msg.weather
        if (!embed) {
          root.style.width = `${Math.round(HUD_BASE_WIDTH * msg.scale)}px`
          root.style.fontSize = `${(BASE_FONT_PX * msg.scale).toFixed(1)}px`
        }
        setLite(msg.lite)
        if (playing) {
          root.classList.toggle('on', shown())
          onChange()
        } else {
          rest()
        }
        return
      }
      // Timelines of older casts are dropped; the result is already saved on the backend either way.
      // (One cast sends several with the same seq: cast, maybe until_bite, resolve. A summary has its own.)
      if (msg.phase !== 'digest' && msg.seq < lastSeq) return
      if (msg.phase !== 'digest') lastSeq = msg.seq
      // The bite window was answered somewhere else (the other HUD, or the backend's own timer).
      if (msg.phase === 'resolve') biteResolve?.(null)
      if (queue.length >= MAX_QUEUE) queue.shift()
      queue.push(msg)
      void drain()
    },
    clickable: () => biteResolve !== null && root.classList.contains('on'),
    visible: () => root.classList.contains('on')
  }
}
