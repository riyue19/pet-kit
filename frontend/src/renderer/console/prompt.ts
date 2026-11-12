import { computed, reactive } from 'vue'
import type { PromptConfig, PromptSectionInfo, PromptState, PromptTemplate, PromptTool, PromptView } from '../../shared/backend'

interface Store {
  loading: boolean
  loaded: boolean
  offline: boolean
  error: string | null
  form: PromptConfig
  /** JSON of the last saved/loaded PromptConfig. */
  snapshot: string
  expressions: string[]
  motions: string[]
  petConnected: boolean
  maxKnowledgeChars: number
  templates: PromptTemplate[]
  sections: PromptSectionInfo[]
  defaultOrder: string[]
  skillLabels: string[]
  tools: PromptTool[]
  last: PromptView | null
  preview: PromptView | null
  previewing: boolean
  previewError: string | null
}

const emptyConfig = (): PromptConfig => ({
  order: [],
  format_rules: '',
  knowledge: [],
  expression_notes: {},
  motion_notes: {},
  search_keywords: [],
  overrides: {},
  skills_mode: 'keyword',
  tool_keywords: {},
  search_use_fallback: true,
  speech_repair: true
})

export const prompt = reactive<Store>({
  loading: false,
  loaded: false,
  offline: false,
  error: null,
  form: emptyConfig(),
  snapshot: '',
  expressions: [],
  motions: [],
  petConnected: false,
  maxKnowledgeChars: 0,
  templates: [],
  sections: [],
  defaultOrder: [],
  skillLabels: [],
  tools: [],
  last: null,
  preview: null,
  previewing: false,
  previewError: null
})

/** Block keys in the order they are sent. Mirrors backend core.agent.section_order. */
export function sectionOrder(order: string[], builtin: string[]): string[] {
  const out = [...new Set(order.filter((k) => builtin.includes(k)))]
  builtin.forEach((key, i) => {
    if (out.includes(key)) return
    const before = builtin
      .slice(0, i)
      .reverse()
      .find((k) => out.includes(k))
    out.splice(before === undefined ? 0 : out.indexOf(before) + 1, 0, key)
  })
  return out
}

const clone =<T>(v: T): T => JSON.parse(JSON.stringify(v)) as T

function serialize(): string {
  return JSON.stringify(prompt.form)
}

export const dirty = computed(() => prompt.loaded && serialize() !== prompt.snapshot)

function applyMeta(s: PromptState): void {
  prompt.expressions = s.expressions
  prompt.motions = s.motions
  prompt.petConnected = s.pet_connected
  prompt.maxKnowledgeChars = s.max_knowledge_chars
  prompt.templates = s.templates
  prompt.sections = s.sections
  prompt.defaultOrder = s.default_order
  prompt.skillLabels = s.skill_labels
  prompt.tools = s.tools
  prompt.last = s.last
}

function apply(s: PromptState): void {
  prompt.form = clone(s.config)
  prompt.snapshot = serialize()
  prompt.loaded = true
  applyMeta(s)
}

export async function loadPrompt(): Promise<void> {
  prompt.loading = true
  try {
    const res = await window.petConsole.backend<PromptState>('GET', '/api/prompt')
    prompt.offline = !res.ok && res.offline
    prompt.error = res.ok ? null : res.error
    if (res.ok) apply(res.data)
  } finally {
    prompt.loading = false
  }
}

export function revertPrompt(): void {
  if (prompt.snapshot) prompt.form = JSON.parse(prompt.snapshot) as PromptConfig
}

/** Saves the form and refreshes it from the returned value. Throws a user-facing message on failure. */
export async function savePrompt(): Promise<void> {
  const res = await window.petConsole.backend<PromptState>('PUT', '/api/prompt', clone(prompt.form))
  if (!res.ok) throw new Error(res.error || '保存失败')
  apply(res.data)
}

// A slower, older preview must not overwrite a newer one.
let previewGeneration = 0

/** Previews with what is in the form now (saved or not). Nothing is sent to the LLM. */
export async function runPreview(text: string): Promise<void> {
  const gen = ++previewGeneration
  prompt.previewing = true
  try {
    const res = await window.petConsole.backend<PromptView>('POST', '/api/prompt/preview', { text, config: clone(prompt.form) })
    if (gen !== previewGeneration) return
    if (res.ok) {
      prompt.preview = res.data
      prompt.previewError = null
    } else {
      prompt.previewError = res.error || '预览失败'
    }
  } finally {
    if (gen === previewGeneration) prompt.previewing = false
  }
}

/** Re-reads only last/expressions/motions/petConnected; unsaved edits stay. */
export async function refreshLast(): Promise<void> {
  const res = await window.petConsole.backend<PromptState>('GET', '/api/prompt')
  if (res.ok) applyMeta(res.data)
}
