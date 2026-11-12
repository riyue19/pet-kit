/**
 * Builds a black-and-white fish as an SVG string from a body template plus patterns
 * (TypeScript port of the fish asset kit's fish-composer.js; shapes in ./data.ts).
 *
 * Modes: 'stamp' (codex print; with `filter` it uses the #ink-stamp filter, which the page must define),
 * 'silhouette' (not discovered yet: shape only), 'icon' (like stamp, never filtered: for the small HUD card).
 * Every call uses a fresh clipPath id, so compose again for each place a fish is shown.
 */
import { FISH_PATTERNS, FISH_TEMPLATES } from './data'

export interface FishTemplate {
  body: string
  fins: string[]
  marks: string[]
  eye: { cx: number; cy: number; r: number }
  inkStrokes?: { d: string; w: number }[]
  /** Patterns drawn along this body (the eel); used instead of the shared ones. */
  patterns?: Record<string, string>
  noGlobalPatterns?: boolean
}

export type FishMode = 'stamp' | 'silhouette' | 'icon'

export interface FishOptions {
  template: string
  patterns?: string[]
  mode?: FishMode
  /** stamp mode only: apply url(#ink-stamp). */
  filter?: boolean
  label?: string
  ink?: string
  paper?: string
}

export const FISH_INK = '#14110f'
export const FISH_PAPER = '#f4efe4'
const COLOR_RE = /^(?:#[0-9a-fA-F]{3,8}|currentColor|var\(--[a-z0-9-]+\))$/
let uid = 0

const esc = (s: string): string => s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c] ?? c)
const color = (v: string | undefined, fallback: string): string => (v && COLOR_RE.test(v) ? v : fallback)

/** Unknown template names fall back to the slim fish; unknown patterns are skipped. */
export function composeFish(opts: FishOptions): string {
  const t = Object.hasOwn(FISH_TEMPLATES, opts.template) ? FISH_TEMPLATES[opts.template] : FISH_TEMPLATES.slim
  const mode = opts.mode ?? 'stamp'
  const ink = color(opts.ink, FISH_INK)
  const paper = color(opts.paper, FISH_PAPER)
  const id = `fc${++uid}`

  const fins = t.fins.map((d) => `<path d="${d}"/>`).join('')
  const strokes = (t.inkStrokes ?? []).map((s) => `<path d="${s.d}" stroke="${ink}" stroke-width="${s.w}" fill="none" stroke-linecap="round"/>`).join('')
  const base = `<g fill="${ink}">${fins}<path d="${t.body}"/></g>${strokes}`

  let detail = ''
  if (mode !== 'silhouette') {
    const drawn = (opts.patterns ?? [])
      .map((p) => {
        let src: string | undefined = t.patterns && Object.hasOwn(t.patterns, p) ? t.patterns[p] : undefined
        if (src === undefined && !t.noGlobalPatterns && Object.hasOwn(FISH_PATTERNS, p)) src = FISH_PATTERNS[p]
        return (src ?? '').split('@P').join(paper)
      })
      .join('')
    const marks = t.marks.map((d) => `<path d="${d}"/>`).join('')
    const e = t.eye
    detail =
      `<g clip-path="url(#${id})">${drawn}</g>` +
      `<g stroke="${paper}" stroke-width="1.6" fill="none">${marks}</g>` +
      `<circle cx="${e.cx}" cy="${e.cy}" r="${e.r}" fill="${paper}"/>` +
      `<circle cx="${e.cx + 0.6}" cy="${e.cy}" r="${(e.r * 0.45).toFixed(1)}" fill="${ink}"/>`
  }
  const filter = mode === 'stamp' && opts.filter === true ? ' filter="url(#ink-stamp)"' : ''
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100" role="img" aria-label="${esc(opts.label ?? 'fish')}">` +
    `<defs><clipPath id="${id}"><path d="${t.body}"/></clipPath></defs><g${filter}>${base}${detail}</g></svg>`
  )
}

/** Junk pulled out of the water (viewBox 0 0 100 100), by fish.yaml junk id; unknown ids get the can. */
const JUNK: Record<string, string> = {
  boot: '<path d="M30,14 L58,14 L58,52 C58,58 66,60 76,64 C88,68 90,78 84,84 L26,84 C22,84 20,80 20,76 L22,30 C22,20 26,14 30,14 Z" fill="@I"/><path d="M18,84 L88,84 L88,91 L18,91 Z" fill="@I"/><path d="M30,22 H58 M32,32 H56 M32,42 H56" stroke="@P" stroke-width="2" fill="none"/><path d="M20,84 H88" stroke="@P" stroke-width="1.5"/>',
  can: '<path d="M30,26 L70,26 L70,80 C70,87 30,87 30,80 Z" fill="@I"/><ellipse cx="50" cy="26" rx="20" ry="5.5" fill="@P" stroke="@I" stroke-width="2"/><path d="M30,46 H70 M30,62 H70" stroke="@P" stroke-width="2"/><path d="M58,24 C60,20 64,20 64,23" stroke="@I" stroke-width="1.6" fill="none"/>',
  bottle: '<g transform="rotate(20 50 50)"><path d="M45,4 H55 V10 H45 Z" fill="@I"/><path d="M44,10 L56,10 L56,26 C56,32 70,38 70,52 L70,84 C70,90 30,90 30,84 L30,52 C30,38 44,32 44,26 Z" fill="@I"/><path d="M42,50 H58 V78 H42 Z" fill="@P"/><path d="M45,56 H55 M45,62 H55 M45,68 H52" stroke="@I" stroke-width="1.6"/></g>'
}

export function composeJunk(id: string, opts: { label?: string; ink?: string; paper?: string } = {}): string {
  const src = Object.hasOwn(JUNK, id) ? JUNK[id] : JUNK.can
  const body = src.split('@I').join(color(opts.ink, FISH_INK)).split('@P').join(color(opts.paper, FISH_PAPER))
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" role="img" aria-label="${esc(opts.label ?? 'junk')}">${body}</svg>`
}
