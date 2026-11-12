/** Fishing HUD messages from the backend (core/games/fishing), relayed main -> pet window after validation. */

export type FishingStepState = 'cast' | 'wait' | 'bite' | 'reel' | 'result' | 'escape' | 'empty'
const STEP_STATES: readonly string[] = ['cast', 'wait', 'bite', 'reel', 'result', 'escape', 'empty']
const RARITIES: readonly string[] = ['common', 'uncommon', 'rare', 'legendary']
const MAX_STEPS = 8
const MAX_STEP_MS = 15000

export type FishingCard =
  | {
      kind: 'fish'
      name: string
      template: string
      patterns: string[]
      sizeCm: number
      rarity: 'common' | 'uncommon' | 'rare' | 'legendary'
      rarityLabel: string
      first: boolean
      trophy: boolean
      record: boolean
    }
  | { kind: 'junk'; name: string; junkId: string }
  | { kind: 'bottle'; sender: string; body: string; coins: number }
  | { kind: 'digest'; n: number; fishCount: number; bottles: number; highlights: string[]; more: number }

export interface FishingStep {
  state: FishingStepState
  /** Milliseconds. */
  dur: number
  /** bite only: the user may click during this step to land the fish. */
  window: boolean
  /** empty only: just a small ripple. */
  minimal: boolean
  card: FishingCard | null
}

export interface FishingState {
  type: 'fishing.state'
  enabled: boolean
  /** Light tier: still pictures, no animation. */
  lite: boolean
  /** The small window next to the pet is shown (false = put away; the console's fishing scene still shows it). */
  placed: boolean
  /** Size of the small window, 0.6..2.5. */
  scale: number
  /** Unix seconds when the line in the water was cast; null = no line out. When it bites is never sent. */
  castTs: number | null
  /** Time of day and weather, for the scene's sky. */
  sky: 'dawn' | 'day' | 'dusk' | 'night'
  weather: 'sunny' | 'cloudy' | 'rain'
}

export interface FishingAnim {
  type: 'fishing.anim'
  seq: number
  /** cast: the line goes out (then it waits, for real); until_bite: a bite window; resolve: how the cast ended; digest: a summary card. */
  phase: 'cast' | 'until_bite' | 'resolve' | 'digest'
  steps: FishingStep[]
  lite: boolean
}

export type FishingMessage = FishingState | FishingAnim

export const FISHING_OFF: FishingState = { type: 'fishing.state', enabled: false, lite: false, placed: true, scale: 1, castTs: null, sky: 'day', weather: 'sunny' }

const str = (v: unknown, max: number): string => (typeof v === 'string' ? v.slice(0, max) : '')
const num = (v: unknown, max: number): number => (typeof v === 'number' && Number.isFinite(v) ? Math.min(max, Math.max(0, v)) : 0)
const names = (v: unknown, count: number, max: number): string[] =>
  Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string').slice(0, count).map((x) => x.slice(0, max)) : []

function toCard(v: unknown): FishingCard | null {
  if (typeof v !== 'object' || v === null) return null
  const c = v as Record<string, unknown>
  switch (c.kind) {
    case 'fish':
      return {
        kind: 'fish',
        name: str(c.name, 12) || '鱼',
        template: str(c.template, 16),
        patterns: names(c.patterns, 4, 16),
        sizeCm: num(c.size_cm, 9999),
        rarity: (RARITIES.includes(c.rarity as string) ? c.rarity : 'common') as 'common',
        rarityLabel: str(c.rarity_label, 8),
        first: c.first === true,
        trophy: c.trophy === true,
        record: c.record === true
      }
    case 'junk':
      return { kind: 'junk', name: str(c.name, 12) || '杂物', junkId: str(c.junk_id, 32) }
    case 'bottle':
      return { kind: 'bottle', sender: str(c.sender, 12), body: str(c.body, 160), coins: Math.round(num(c.coins, 9999)) }
    case 'digest':
      return {
        kind: 'digest',
        n: Math.round(num(c.n, 9999)),
        fishCount: Math.round(num(c.fish_count, 9999)),
        bottles: Math.round(num(c.bottles, 9999)),
        highlights: names(c.highlights, 3, 12),
        more: Math.round(num(c.more, 9999))
      }
    default:
      return null
  }
}

/** Validates a fishing message from the backend; unknown or malformed ones yield null. */
export function toFishingMessage(msg: Record<string, unknown>): FishingMessage | null {
  if (msg.type === 'fishing.state') {
    const cast = msg.cast_ts
    const scale = msg.scale
    return {
      type: 'fishing.state',
      enabled: msg.enabled === true,
      lite: msg.lite === true,
      placed: msg.placed !== false,
      scale: typeof scale === 'number' && Number.isFinite(scale) ? Math.min(2.5, Math.max(0.6, scale)) : 1,
      castTs: typeof cast === 'number' && Number.isFinite(cast) ? cast : null,
      sky: (['dawn', 'day', 'dusk', 'night'].includes(msg.time_slot as string) ? msg.time_slot : 'day') as FishingState['sky'],
      weather: (['sunny', 'cloudy', 'rain'].includes(msg.weather as string) ? msg.weather : 'sunny') as FishingState['weather']
    }
  }
  if (msg.type !== 'fishing.anim') return null
  const { seq, phase } = msg
  if (typeof seq !== 'number' || !Number.isInteger(seq) || seq < 0) return null
  if (phase !== 'cast' && phase !== 'until_bite' && phase !== 'resolve' && phase !== 'digest') return null
  if (!Array.isArray(msg.steps)) return null
  const steps: FishingStep[] = []
  for (const raw of msg.steps.slice(0, MAX_STEPS)) {
    if (typeof raw !== 'object' || raw === null) return null
    const s = raw as Record<string, unknown>
    if (typeof s.state !== 'string' || !STEP_STATES.includes(s.state)) return null
    steps.push({
      state: s.state as FishingStepState,
      dur: Math.max(100, num(s.dur, MAX_STEP_MS)),
      window: s.state === 'bite' && s.window === true,
      minimal: s.minimal === true,
      card: s.state === 'result' ? toCard(s.card) : null
    })
  }
  return { type: 'fishing.anim', seq, phase, steps, lite: msg.lite === true }
}
