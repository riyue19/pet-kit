<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  NAlert,
  NButton,
  NCard,
  NCollapse,
  NCollapseItem,
  NDynamicTags,
  NEmpty,
  NInput,
  NRadioButton,
  NRadioGroup,
  NSelect,
  NSpace,
  NSpin,
  NSwitch,
  NTag
} from 'naive-ui'
import BackendOffline from '../components/BackendOffline.vue'
import PageHead from '../components/PageHead.vue'
import SaveBar from '../components/SaveBar.vue'
import { activePage } from '../store'
import { useTask } from '../task'
import { useDragSort } from '../drag-sort'
import { dirty, loadPrompt, prompt, refreshLast, revertPrompt, runPreview, savePrompt, sectionOrder } from '../prompt'
import type { PromptSection, PromptSectionInfo } from '../../../shared/backend'

const PREVIEW_DELAY_MS = 600

const { busy: saving, run } = useTask()
const mode = ref<'edit' | 'last'>('edit')
const previewText = ref('')

const sourceLabel: Record<string, string> = {
  persona: '人设页',
  prompt: '本页',
  memory: '记忆页',
  labels: '本页',
  skills: '指令页',
  llm: 'AI 模型页',
  screen: '感知与主动页',
  stickers: '表情包页',
  status: '状态页'
}
const sourceText = (s: string | null): string => (s ? (sourceLabel[s] ?? s) : '内置')

const modeOptions = [
  { label: '总是带上', value: 'always' },
  { label: '提到关键词时', value: 'keyword' }
]

// "上一次真实发送" is only read on demand, so fetch it when the tab is opened.
watch(mode, (m) => {
  if (m === 'last') void refreshLast()
})

function fmtTime(ts?: number): string {
  return ts ? new Date(ts * 1000).toLocaleString() : ''
}

// The preview follows the form (saved or not): re-run a moment after the last edit.
let previewTimer: number | null = null
function schedulePreview(): void {
  if (previewTimer !== null) clearTimeout(previewTimer)
  previewTimer = window.setTimeout(() => {
    previewTimer = null
    if (prompt.loaded) void runPreview(previewText.value)
  }, PREVIEW_DELAY_MS)
}
watch(() => [JSON.stringify(prompt.form), previewText.value], schedulePreview)
onBeforeUnmount(() => {
  if (previewTimer !== null) clearTimeout(previewTimer)
})

onMounted(async () => {
  // Re-read unless there are unsaved edits.
  if (!prompt.loaded || !dirty.value) await loadPrompt()
  if (prompt.loaded) void runPreview(previewText.value)
})

const save = (): Promise<boolean> => run(savePrompt, '已保存，下一句对话生效')

// --- The list: every block the prompt can contain, in the order it is sent ---
const rows = computed<PromptSectionInfo[]>(() =>
  sectionOrder(prompt.form.order, prompt.defaultOrder)
    .map((k) => prompt.sections.find((s) => s.key === k))
    .filter((s): s is PromptSectionInfo => !!s)
)
const sent = computed(() => {
  const out: Record<string, PromptSection> = {}
  for (const s of prompt.preview?.sections ?? []) out[s.key] = s
  return out
})
const drag = useDragSort({
  selector: '.sec-item',
  handle: '.n-collapse-item__header',
  keys: () => rows.value.map((r) => r.key),
  move: (from, to) => {
    const keys = rows.value.map((r) => r.key)
    const [k] = keys.splice(from, 1)
    keys.splice(to, 0, k)
    // The built-in order is stored as "no order", so an undone drag is not an unsaved change.
    prompt.form.order = keys.join() === prompt.defaultOrder.join() ? [] : keys
  }
})

// --- Knowledge ---
const addKnowledge = (): void => {
  prompt.form.knowledge.push({ title: '', content: '', enabled: true, mode: 'always', keywords: [] })
}
const removeKnowledge = (i: number): void => {
  prompt.form.knowledge.splice(i, 1)
}
const alwaysChars = computed(() =>
  prompt.form.knowledge.filter((k) => k.enabled && k.mode === 'always').reduce((n, k) => n + k.content.length, 0)
)
type Knowledge = (typeof prompt.form.knowledge)[number]
function entryError(k: Knowledge): string | null {
  if (!k.title.trim() || !k.content.trim()) return '标题和内容不能为空'
  if (k.mode === 'keyword' && k.keywords.length === 0) return '请至少填一个关键词'
  return null
}
// Why 保存 is disabled, naming the entry: the list may be folded away when the user reaches the save bar.
const problem = computed<string | null>(() => {
  const i = prompt.form.knowledge.findIndex((k) => entryError(k) !== null)
  if (i < 0) return null
  const k = prompt.form.knowledge[i]
  const section = prompt.sections.find((s) => s.key === 'knowledge')?.title ?? '知识库'
  const name = k.title.trim() ? `（${k.title.trim()}）` : ''
  return `「${section}」里的第 ${i + 1} 条${name}：${entryError(k)}`
})

// --- Label notes ---
function setNote(kind: 'expression_notes' | 'motion_notes', label: string, v: string): void {
  const notes = { ...prompt.form[kind] }
  if (v) notes[label] = v
  else delete notes[label]
  prompt.form[kind] = notes
}
const extraLabels = computed(() => {
  const out: string[] = []
  for (const k of Object.keys(prompt.form.expression_notes)) if (!prompt.expressions.includes(k)) out.push(k)
  for (const k of Object.keys(prompt.form.motion_notes)) if (!prompt.motions.includes(k)) out.push(k)
  return out
})

// --- Built-in texts: each is edited whole. An edit equal to the built-in text (or blank) is dropped. ---
const templateOf = (key: string) => prompt.templates.find((t) => t.key === key)
const templateText = (key: string): string => prompt.form.overrides[key] ?? templateOf(key)?.default ?? ''
const templateEdited = (key: string): boolean => key in prompt.form.overrides
function setTemplate(key: string, v: string): void {
  const next = { ...prompt.form.overrides }
  if (!v.trim() || v.trim() === templateOf(key)?.default.trim()) delete next[key]
  else next[key] = v
  prompt.form.overrides = next
}
const resetTemplate = (key: string): void => setTemplate(key, '')
// Required placeholders the edited text no longer contains.
const missing = (key: string): string[] => (templateOf(key)?.required ?? []).filter((n) => !templateText(key).includes(`{${n}}`))
const brace = (n: string): string => `{${n}}`
const rowEdited = (s: PromptSectionInfo): boolean => s.templates.some(templateEdited)

// --- Commands ---
const skillsModeOptions = [
  { label: '提到名称（关键词）时才带上', value: 'keyword' },
  { label: '每句话都带上全部指令', value: 'always' }
]
// An action newer than the saved file has no entry yet: the backend then uses its built-in words, so show those.
const toolWords = (name: string): string[] => prompt.form.tool_keywords[name] ?? prompt.tools.find((t) => t.name === name)?.defaults ?? []
function setToolWords(name: string, v: string[]): void {
  prompt.form.tool_keywords = { ...prompt.form.tool_keywords, [name]: v }
}

const emptyLabelsText = '当前模型还没有表情/动作标签，请先在「形象」页设置'
</script>

<template>
  <page-head title="提示词" />
  <n-spin :show="prompt.loading && !prompt.loaded">
    <backend-offline v-if="prompt.offline || (!prompt.loaded && prompt.error)" :error="prompt.error" :loading="prompt.loading" @retry="loadPrompt" />
    <n-space v-else-if="prompt.loaded" vertical :size="20">
      <n-card title="实际发送的提示词">
        <n-space vertical :size="12">
          <n-radio-group v-model:value="mode">
            <n-radio-button value="edit">编辑与预览</n-radio-button>
            <n-radio-button value="last">上一次真实发送</n-radio-button>
          </n-radio-group>

          <template v-if="mode === 'edit'">
            <ul class="tips">
              <li>下面是系统提示词的全部段落，从上到下就是发给模型的顺序；按住一条的标题拖动可以调整顺序。</li>
              <li>展开一条可以改它的文字和相关设置，「发出去是这样」会跟着实时变化（包括还没保存的修改）。</li>
              <li>「条件触发」的段落什么时候带上由程序判断，这里只能改文字。</li>
              <li>改完点底部的「保存」，下一句对话生效。</li>
            </ul>
            <n-space align="center" :wrap="false">
              <n-input v-model:value="previewText" :maxlength="500" clearable placeholder="假设你说了这句话…（留空 = 你好呀）" style="width: 420px" />
              <n-spin v-if="prompt.previewing" :size="16" />
            </n-space>
            <n-alert v-if="prompt.previewError" type="error" :closable="false">预览失败：{{ prompt.previewError }}</n-alert>
            <template v-if="prompt.preview">
              <n-alert v-for="(n, i) in prompt.preview.notes" :key="i" type="warning" :closable="false">{{ n }}</n-alert>
              <span class="hint">
                模型配置：{{ prompt.preview.model ?? '未指定' }} · 这句话的系统提示词共 {{ prompt.preview.total_chars }} 字 · 联网搜索：{{ prompt.preview.search ? '开' : '关' }} ·
                朗读语言：{{ prompt.preview.speech_language ?? '和气泡相同' }}
              </span>
            </template>

            <div class="sec-list" @pointerdown="drag.start">
              <n-collapse>
                <n-collapse-item v-for="s in rows" :key="s.key" :name="s.key" class="sec-item">
                  <template #header>
                    <span class="grip" title="按住拖动调整顺序">⠿</span>
                    <span>{{ s.title }}</span>
                  </template>
                  <template #header-extra>
                    <n-space :size="6" :wrap="false">
                      <n-tag v-if="rowEdited(s)" size="small" type="warning" :bordered="false">已修改</n-tag>
                      <n-tag size="small" :bordered="false" :type="s.always ? 'default' : 'info'">{{ s.always ? '每次都带' : '条件触发' }}</n-tag>
                      <n-tag v-if="sent[s.key]" size="small" type="success" :bordered="false">这句会发 · {{ sent[s.key].chars }} 字</n-tag>
                      <n-tag v-else size="small" :bordered="false" class="dim">这句不发</n-tag>
                    </n-space>
                  </template>

                  <n-space vertical :size="12">
                    <span class="hint">什么时候带上：{{ s.condition }}。<template v-if="s.source && s.source !== 'prompt' && s.source !== 'labels'">内容来自{{ sourceText(s.source) }}。</template></span>

                    <!-- Settings that belong to this block -->
                    <template v-if="s.key === 'persona'">
                      <n-space align="center">
                        <span class="hint">人设只在「人设」页修改，这里只显示发出去的内容。</span>
                        <n-button size="small" @click="activePage = 'persona'">去「人设」页</n-button>
                      </n-space>
                    </template>

                    <template v-else-if="s.key === 'skills'">
                      <div class="sub">触发方式</div>
                      <n-select v-model:value="prompt.form.skills_mode" :options="skillsModeOptions" style="width: 280px" />
                      <span class="hint">「提到名称时才带上」：你这句话（或上一句）里出现「指令」页里某条指令的名称，才把那一条的说明放进这一段，平时聊天不带，省字数也不容易误触发。没带说明的指令这一句不能被调用。</span>
                      <template v-if="prompt.form.skills_mode === 'keyword'">
                        <div class="sub">会触发的名称（来自「指令」页）</div>
                        <n-space v-if="prompt.skillLabels.length" :size="6">
                          <n-tag v-for="l in prompt.skillLabels" :key="l" size="small" :bordered="false">{{ l }}</n-tag>
                        </n-space>
                        <span v-else class="hint">「指令」页还没有可用的指令。</span>
                        <div class="sub">放音乐和内置动作：用下面的触发词</div>
                        <div v-for="t in prompt.tools" :key="t.name" class="row top">
                          <span class="label">{{ t.title }}</span>
                          <n-space vertical :size="4" style="flex: 1">
                            <n-dynamic-tags :value="toolWords(t.name)" :max="30" @update:value="(v: string[]) => setToolWords(t.name, v)" />
                            <n-button size="tiny" @click="setToolWords(t.name, [...t.defaults])">恢复默认触发词</n-button>
                          </n-space>
                        </div>
                        <span class="hint">触发词清空 = 这一项每句话都带上。</span>
                      </template>
                    </template>

                    <template v-else-if="s.key === 'knowledge'">
                      <div class="sub">条目</div>
                      <span class="hint">写给桌宠的设定和资料。「总是带上」每句话都发给模型（越多越贵）；「提到关键词时」只有你这句话里出现关键词才带上。不要写密码、密钥。</span>
                      <div v-for="(k, i) in prompt.form.knowledge" :key="i" class="entry" :class="{ bad: entryError(k) !== null }">
                        <n-space vertical :size="8">
                          <n-space align="center">
                            <n-switch v-model:value="k.enabled" />
                            <n-input v-model:value="k.title" :maxlength="40" placeholder="标题" style="width: 220px" />
                            <n-select v-model:value="k.mode" :options="modeOptions" style="width: 160px" />
                            <n-button size="small" @click="removeKnowledge(i)">删除</n-button>
                          </n-space>
                          <n-space v-if="k.mode === 'keyword'" align="center" :size="8">
                            <span class="hint">关键词</span>
                            <n-dynamic-tags v-model:value="k.keywords" :max="20" />
                          </n-space>
                          <n-input v-model:value="k.content" type="textarea" :maxlength="2000" show-count :autosize="{ minRows: 2, maxRows: 8 }" placeholder="内容" />
                          <span v-if="entryError(k)" class="hint over">{{ entryError(k) }}</span>
                        </n-space>
                      </div>
                      <n-button :disabled="prompt.form.knowledge.length >= 50" @click="addKnowledge">添加一条</n-button>
                      <span class="hint" :class="{ over: alwaysChars > prompt.maxKnowledgeChars }">
                        「总是带上」的条目共 {{ alwaysChars }} 字 / 上限 {{ prompt.maxKnowledgeChars }} 字<template v-if="alwaysChars > prompt.maxKnowledgeChars">，超出的条目不会发送</template>
                      </span>
                    </template>

                    <template v-else-if="s.key === 'labels'">
                      <div class="sub">表情和动作说明</div>
                      <span class="hint">这里列出的是当前模型在「形象」页定义的标签。给标签写一句什么时候用，就会跟在标签名后面发出去；不填就只发标签名。</span>
                      <n-alert v-if="!prompt.petConnected" type="info" :closable="false">桌宠未连接，读不到当前模型的标签。</n-alert>
                      <div class="sub">表情</div>
                      <n-empty v-if="!prompt.expressions.length" :description="emptyLabelsText" />
                      <div v-for="l in prompt.expressions" :key="`e-${l}`" class="row">
                        <code class="label">{{ l }}</code>
                        <n-input :value="prompt.form.expression_notes[l] ?? ''" :maxlength="100" placeholder="什么时候用" @update:value="(v: string) => setNote('expression_notes', l, v)" />
                      </div>
                      <div class="sub">动作</div>
                      <n-empty v-if="!prompt.motions.length" :description="emptyLabelsText" />
                      <div v-for="l in prompt.motions" :key="`m-${l}`" class="row">
                        <code class="label">{{ l }}</code>
                        <n-input :value="prompt.form.motion_notes[l] ?? ''" :maxlength="100" placeholder="什么时候用" @update:value="(v: string) => setNote('motion_notes', l, v)" />
                      </div>
                      <span v-if="extraLabels.length" class="hint">另有当前模型没有的标签说明已保留：{{ extraLabels.join('、') }}</span>
                    </template>

                    <template v-else-if="s.key === 'stickers'">
                      <n-space align="center">
                        <span class="hint">图片和备注名在「表情包」页管理；这里只改这一段的说明文字。</span>
                        <n-button size="small" @click="activePage = 'stickers'">去「表情包」页</n-button>
                      </n-space>
                    </template>

                    <template v-else-if="s.key === 'search'">
                      <n-space align="center">
                        <n-switch v-model:value="prompt.form.search_use_fallback" />
                        <span>主用模型不能搜索时，需要搜索的问题改用能搜索的备选模型回答</span>
                      </n-space>
                      <div class="sub">额外的搜索关键词</div>
                      <n-dynamic-tags v-model:value="prompt.form.search_keywords" :max="50" />
                      <span class="hint">这句话里出现这些词（或内置的：最新、新闻、价格、比分、查一下、搜一下…）就会提示模型先搜索。</span>
                    </template>

                    <template v-else-if="s.key === 'format'">
                      <n-space align="center">
                        <n-switch v-model:value="prompt.form.speech_repair" />
                        <span>日语/英语朗读时，模型漏写朗读内容就自动补翻译一次</span>
                      </n-space>
                      <span class="hint">关掉后，漏写时会直接用中文气泡文字朗读。每次补救多一次很小的模型调用。</span>
                    </template>

                    <template v-else-if="s.key === 'format_rules'">
                      <n-input v-model:value="prompt.form.format_rules" type="textarea" :maxlength="2000" show-count :autosize="{ minRows: 3, maxRows: 10 }" placeholder="例如：不要用颜文字；回答问题先给结论。" />
                      <span class="hint">写了什么就原样作为这一段发出去；留空就没有这一段。</span>
                    </template>

                    <!-- The block's own text(s) -->
                    <div v-for="key in s.templates" :key="key" class="tpl">
                      <n-space align="center" :size="8">
                        <span class="sub">{{ s.templates.length > 1 ? templateOf(key)?.title : '编辑这一段' }}</span>
                        <n-tag v-if="templateEdited(key)" size="small" type="warning" :bordered="false">已修改</n-tag>
                        <n-button v-if="templateEdited(key)" size="tiny" @click="resetTemplate(key)">恢复默认</n-button>
                      </n-space>
                      <n-input :value="templateText(key)" type="textarea" :maxlength="4000" show-count :autosize="{ minRows: 2, maxRows: 18 }" @update:value="(v: string) => setTemplate(key, v)" />
                      <span v-if="templateOf(key)?.placeholders.length" class="hint">
                        花括号的位置由程序填入，可以挪动或删掉：
                        <template v-for="ph in templateOf(key)?.placeholders" :key="ph.name"><code>{{ brace(ph.name) }}</code> = {{ ph.desc }}；</template>
                      </span>
                      <n-alert v-for="n in missing(key)" :key="n" type="warning" :closable="false">这一段里没有 {{ brace(n) }}，对应的内容不会发给模型。</n-alert>
                    </div>

                    <div>
                      <div class="sub out-title">发出去是这样</div>
                      <pre v-if="sent[s.key]" class="block out">{{ sent[s.key].text }}</pre>
                      <span v-else class="hint">这句话不会带上这一段（空）。换一句能触发它的话试试。</span>
                    </div>
                  </n-space>
                </n-collapse-item>
              </n-collapse>
            </div>
            <n-space v-if="prompt.form.order.length" align="center">
              <n-button size="small" @click="prompt.form.order = []">恢复默认顺序</n-button>
              <span class="hint">默认把「输出格式」放在最后，因为模型对末尾的要求遵守得最好。</span>
            </n-space>

            <n-collapse v-if="prompt.preview">
              <n-collapse-item name="__history" :title="`历史对话 · ${prompt.preview.history.length} 条（跟在系统提示词后面）`">
                <pre v-for="(m, i) in prompt.preview.history" :key="i" class="block">{{ m.role === 'user' ? '主人：' : '桌宠：' }}{{ m.content }}</pre>
                <span v-if="!prompt.preview.history.length" class="hint">无</span>
              </n-collapse-item>
              <n-collapse-item name="__user" title="这一句（用户消息）">
                <pre class="block">{{ prompt.preview.user }}</pre>
              </n-collapse-item>
            </n-collapse>
          </template>

          <template v-else>
            <n-space align="center">
              <n-button @click="refreshLast">刷新</n-button>
              <span v-if="prompt.last" class="hint">{{ fmtTime(prompt.last.ts) }} · {{ prompt.last.event_type }}</span>
              <n-tag v-if="prompt.last?.speech_repaired" size="small" :bordered="false">朗读内容是补翻译的</n-tag>
            </n-space>
            <n-empty v-if="!prompt.last" description="后端启动后还没有发送过" />
            <template v-else>
              <n-alert v-if="prompt.last.error" type="error" :closable="false">{{ prompt.last.error }}</n-alert>
              <n-alert v-for="(n, i) in prompt.last.notes" :key="i" type="warning" :closable="false">{{ n }}</n-alert>
              <span class="hint">
                模型配置：{{ prompt.last.model ?? '未指定' }} · 系统提示词共 {{ prompt.last.total_chars }} 字 · 联网搜索：{{ prompt.last.search ? '开' : '关' }} · 朗读语言：{{
                  prompt.last.speech_language ?? '和气泡相同'
                }}
              </span>
              <n-collapse>
                <n-collapse-item v-for="s in prompt.last.sections" :key="s.key" :name="s.key">
                  <template #header>{{ s.title }} · {{ s.chars }} 字</template>
                  <template #header-extra>
                    <n-tag size="small" :bordered="false">{{ sourceText(s.source) }}</n-tag>
                  </template>
                  <pre class="block">{{ s.text }}</pre>
                </n-collapse-item>
                <n-collapse-item name="__history" :title="`历史对话 · ${prompt.last.history.length} 条`">
                  <pre v-for="(m, i) in prompt.last.history" :key="i" class="block">{{ m.role === 'user' ? '主人：' : '桌宠：' }}{{ m.content }}</pre>
                  <span v-if="!prompt.last.history.length" class="hint">无</span>
                </n-collapse-item>
                <n-collapse-item name="__user" title="这一句（用户消息）">
                  <pre class="block">{{ prompt.last.user }}</pre>
                </n-collapse-item>
                <n-collapse-item v-if="prompt.last.raw" name="__raw" title="模型原始回复">
                  <pre class="block">{{ prompt.last.raw }}</pre>
                </n-collapse-item>
              </n-collapse>
            </template>
          </template>
        </n-space>
      </n-card>
    </n-space>
  </n-spin>
  <save-bar v-if="prompt.loaded && !prompt.offline" :dirty="dirty" :saving="saving" :problem="problem" @save="save" @revert="revertPrompt" />
</template>

<style scoped>
.hint {
  line-height: 1.6;
}
.tips {
  margin: 0;
  padding-left: 1.2em;
  font-size: 0.9em;
  line-height: 1.7;
  opacity: 0.65;
}
.over {
  color: #d03050;
  opacity: 1;
}
.dim {
  opacity: 0.55;
}
.grip {
  margin-right: 8px;
  opacity: 0.45;
  cursor: grab;
}
.block {
  white-space: pre-wrap;
  word-break: break-word;
  font-family: ui-monospace, Consolas, monospace;
  font-size: 0.9em;
  margin: 0;
}
.out {
  padding: 8px 10px;
  border-radius: 6px;
  background: rgba(128, 128, 128, 0.1);
}
.entry {
  padding-bottom: 12px;
  border-bottom: 1px solid rgba(128, 128, 128, 0.25);
}
.entry.bad {
  padding-left: 10px;
  border-left: 3px solid #d03050;
}
.sub {
  font-weight: 600;
}
.out-title {
  margin: 0 0 6px;
}
.tpl {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.row.top {
  align-items: flex-start;
}
.row {
  display: flex;
  align-items: center;
  gap: 12px;
}
.label {
  width: 120px;
  flex: none;
  font-family: ui-monospace, Consolas, monospace;
}
</style>
