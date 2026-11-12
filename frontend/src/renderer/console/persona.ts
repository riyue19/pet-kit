import { computed, reactive } from 'vue'
import { clone } from './util'
import type { Persona } from '../../shared/backend'

interface Store {
  loading: boolean
  loaded: boolean
  offline: boolean
  error: string | null
  form: Persona
  /** JSON of the last saved/loaded Persona. */
  snapshot: string
}

const emptyPersona = (): Persona => ({ name: '', system_prompt: '', history_turns: 0, max_reply_chars: 100 })

export const persona = reactive<Store>({
  loading: false,
  loaded: false,
  offline: false,
  error: null,
  form: emptyPersona(),
  snapshot: ''
})


function serialize(): string {
  return JSON.stringify(persona.form)
}

export const dirty = computed(() => persona.loaded && serialize() !== persona.snapshot)

export const nameError = computed(() => (persona.form.name.trim() ? null : '名字不能为空'))
export const promptError = computed(() => (persona.form.system_prompt.trim() ? null : '人设提示词不能为空'))
const numbersOk = computed(
  () =>
    Number.isInteger(persona.form.history_turns) &&
    persona.form.history_turns >= 0 &&
    persona.form.history_turns <= 50 &&
    Number.isInteger(persona.form.max_reply_chars) &&
    persona.form.max_reply_chars >= 10 &&
    persona.form.max_reply_chars <= 500
)
export const valid = computed(() => !nameError.value && !promptError.value && numbersOk.value)

function apply(p: Persona): void {
  persona.form = clone(p)
  persona.snapshot = serialize()
  persona.loaded = true
}

export async function loadPersona(): Promise<void> {
  persona.loading = true
  try {
    const res = await window.petConsole.backend<Persona>('GET', '/api/persona')
    persona.offline = !res.ok && res.offline
    persona.error = res.ok ? null : res.error
    if (res.ok) apply(res.data)
  } finally {
    persona.loading = false
  }
}

export function revertPersona(): void {
  if (persona.snapshot) persona.form = JSON.parse(persona.snapshot) as Persona
}

/** Saves the form and refreshes it from the returned value. Throws a user-facing message on failure. */
export async function savePersona(): Promise<void> {
  const body: Persona = { ...clone(persona.form), name: persona.form.name.trim() }
  const res = await window.petConsole.backend<Persona>('PUT', '/api/persona', body)
  if (!res.ok) throw new Error(res.error)
  apply(res.data)
}
