import { computed, reactive } from 'vue'
import { clone } from './util'
import { HOTKEY_ID_RE, type Hotkey, type HotkeysState, type HotkeyStatus } from '../../shared/hotkeys'

interface Store {
  loading: boolean
  loaded: boolean
  error: string | null
  list: Hotkey[]
  status: HotkeyStatus[]
  file: string
  /** Problems reading hotkeys.yaml (HotkeysState.error); saving would drop the skipped entries. */
  fileError: string | null
  expressionChoices: string[]
  motionChoices: string[]
  /** JSON of the last saved/loaded list. */
  snapshot: string
}

export const hotkeys = reactive<Store>({
  loading: false,
  loaded: false,
  error: null,
  list: [],
  status: [],
  file: '',
  fileError: null,
  expressionChoices: [],
  motionChoices: [],
  snapshot: ''
})


// Stable v-for keys: an item's id is editable, so it can't be the key (the card would remount per keystroke).
const uids = new WeakMap<Hotkey, number>()
let nextUid = 1
export function uidOf(h: Hotkey): number {
  let u = uids.get(h)
  if (u === undefined) {
    u = nextUid++
    uids.set(h, u)
  }
  return u
}

function serialize(): string {
  return JSON.stringify(hotkeys.list)
}

export const dirty = computed(() => hotkeys.loaded && serialize() !== hotkeys.snapshot)

function apply(state: HotkeysState): void {
  hotkeys.list = clone(state.hotkeys)
  hotkeys.status = clone(state.status)
  hotkeys.file = state.file
  hotkeys.fileError = state.error
  hotkeys.snapshot = serialize()
  hotkeys.loaded = true
}

export async function loadHotkeys(): Promise<void> {
  hotkeys.loading = true
  try {
    apply(await window.petConsole.hotkeys())
    hotkeys.error = null
  } catch (e) {
    hotkeys.error = e instanceof Error ? e.message : String(e)
  } finally {
    hotkeys.loading = false
  }
  try {
    const res = await window.petConsole.modelActions()
    hotkeys.expressionChoices = Object.keys(res.map.expressions)
    hotkeys.motionChoices = Object.keys(res.map.motions)
  } catch {
    // Labels are only suggestions; leave the lists as they were.
  }
}

export function revertHotkeys(): void {
  if (hotkeys.snapshot) hotkeys.list = JSON.parse(hotkeys.snapshot) as Hotkey[]
}

/** Saves the list and refreshes everything from the returned state. Throws on failure; returns the new state. */
export async function saveHotkeysList(list: Hotkey[]): Promise<HotkeysState> {
  const state = await window.petConsole.saveHotkeys(clone(list))
  apply(state)
  return state
}

export function statusOf(id: string): HotkeyStatus | undefined {
  return hotkeys.status.find((s) => s.id === id)
}

/** True when this item differs from (or is missing in) the last saved list. */
export function itemDirty(h: Hotkey): boolean {
  const saved = (JSON.parse(hotkeys.snapshot || '[]') as Hotkey[]).find((s) => s.id === h.id)
  return !saved || JSON.stringify(saved) !== JSON.stringify(h)
}

export function newHotkey(): Hotkey {
  const used = new Set(hotkeys.list.map((h) => h.id))
  let n = hotkeys.list.length + 1
  while (used.has(`hk_${n}`)) n++
  const id = `hk_${n}`
  if (!HOTKEY_ID_RE.test(id)) throw new Error('无法生成 id')
  return {
    id,
    enabled: true,
    name: '',
    keys: '',
    action: 'reaction',
    builtin: null,
    lines: [],
    expressions: [],
    motions: [],
    prompt: '',
    cooldown_seconds: 2
  }
}
