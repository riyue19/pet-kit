<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import {
  NAlert,
  NButton,
  NCard,
  NEmpty,
  NInput,
  NInputNumber,
  NRadioButton,
  NRadioGroup,
  NSelect,
  NSpace,
  NSpin,
  NTag,
  useMessage,
  type SelectOption
} from 'naive-ui'
import type { EventEntry, InjectRequest } from '../../../shared/backend'
import BackendOffline from '../components/BackendOffline.vue'
import PageHead from '../components/PageHead.vue'
import { eventsStore, injectEvent, pollEvents, summarize } from '../events'
import { errorText } from '../store'
import { clockTime } from '../util'

const POLL_MS = 1500

const message = useMessage()
const filterType = ref<string | null>(null)
const paused = ref(false)
const expanded = reactive<Record<number, boolean>>({})
const highlightId = ref<string | null>(null)

const injectType = ref<string | null>(null)
const injectText = ref('')
const injectDuration = ref<number | null>(0)
const injectArea = ref<'head' | 'body'>('head')
const sending = ref(false)

let timer: ReturnType<typeof setInterval> | null = null
let highlightTimer: ReturnType<typeof setTimeout> | null = null

// The backend's list of event types; with an older backend, the types seen so far.
const filterOptions = computed<SelectOption[]>(() => {
  const types = eventsStore.types.length ? eventsStore.types : [...new Set(eventsStore.events.map((e) => e.type))]
  return types.map((t) => ({ label: t, value: t }))
})
const injectOptions = computed<SelectOption[]>(() => eventsStore.injectable.map((t) => ({ label: t, value: t })))
const rows = computed(() => {
  const list = filterType.value ? eventsStore.events.filter((e) => e.type === filterType.value) : eventsStore.events
  return [...list].reverse()
})
const isText = computed(() => injectType.value === 'user.text' || injectType.value === 'voice.utterance')
const canSend = computed(() => {
  if (!injectType.value) return false
  if (isText.value) return injectText.value.trim().length > 0
  return true
})

function start(): void {
  if (timer !== null) return
  timer = setInterval(() => {
    if (!paused.value) void pollEvents()
  }, POLL_MS)
}

onMounted(() => {
  void pollEvents()
  start()
})

onUnmounted(() => {
  if (timer !== null) clearInterval(timer)
  timer = null
  if (highlightTimer !== null) clearTimeout(highlightTimer)
})

function togglePause(): void {
  paused.value = !paused.value
  if (!paused.value) void pollEvents()
}

async function copyId(id: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(id)
    message.success('已复制')
  } catch (e) {
    message.error(errorText(e))
  }
}

function detail(e: EventEntry): string {
  return JSON.stringify({ payload: e.payload, result: e.result }, null, 2)
}

function buildBody(): InjectRequest | null {
  if (!injectType.value) return null
  if (injectType.value === 'pet.interaction') {
    return { type: injectType.value, payload: { kind: 'click', area: injectArea.value } }
  }
  const payload: Record<string, unknown> = { text: injectText.value.trim() }
  if (injectType.value === 'voice.utterance') payload.duration = injectDuration.value ?? 0
  return { type: injectType.value, payload }
}

async function send(): Promise<void> {
  const body = buildBody()
  if (!body) return
  sending.value = true
  try {
    const id = await injectEvent(body)
    message.success(`已注入 ${id}`)
    highlightId.value = id
    if (highlightTimer !== null) clearTimeout(highlightTimer)
    highlightTimer = setTimeout(() => {
      highlightId.value = null
    }, 2500)
    await pollEvents()
  } catch (e) {
    message.error(errorText(e), { duration: 6000 })
  } finally {
    sending.value = false
  }
}
</script>

<template>
  <page-head title="事件" />
  <n-spin :show="eventsStore.loading && !eventsStore.loaded">
    <backend-offline
      v-if="eventsStore.offline || (!eventsStore.loaded && eventsStore.error)"
      :error="eventsStore.error"
      :loading="eventsStore.loading"
      @retry="pollEvents"
    />
    <n-space v-else-if="eventsStore.loaded" vertical :size="20">
      <n-card title="事件流">
        <n-space vertical :size="12">
          <n-space align="center">
            <n-select v-model:value="filterType" :options="filterOptions" clearable placeholder="全部类型" style="width: 200px" />
            <n-button @click="togglePause">{{ paused ? '继续' : '暂停' }}</n-button>
            <span v-if="paused" class="hint">已暂停刷新</span>
          </n-space>
          <n-empty v-if="rows.length === 0" description="暂无事件" />
          <div v-else class="list">
            <div v-for="e in rows" :key="e.seq" class="row" :class="{ hl: e.id === highlightId }">
              <div class="line">
                <span class="time">{{ clockTime(e.ts) }}</span>
                <n-tag size="small" :bordered="false">{{ e.type }}</n-tag>
                <span class="id" title="点击复制" @click="copyId(e.id)">{{ e.id }}</span>
                <span class="summary">{{ summarize(e) }}</span>
                <n-button text size="tiny" @click="expanded[e.seq] = !expanded[e.seq]">{{ expanded[e.seq] ? '收起' : '详情' }}</n-button>
              </div>
              <div class="result">
                <template v-if="e.result?.output">
                  <span>→ {{ e.result.output.text }}</span>
                  <n-tag v-if="e.result.output.expression" size="tiny" type="info" :bordered="false">{{ e.result.output.expression }}</n-tag>
                  <n-tag v-if="e.result.output.motion" size="tiny" type="success" :bordered="false">{{ e.result.output.motion }}</n-tag>
                </template>
                <span v-else-if="e.result?.error" class="error">{{ e.result.error }}</span>
                <span v-else class="hint">处理中/无输出</span>
              </div>
              <pre v-if="expanded[e.seq]" class="detail">{{ detail(e) }}</pre>
            </div>
          </div>
        </n-space>
      </n-card>

      <n-card title="注入事件">
        <n-space vertical :size="12">
          <n-alert type="warning" :show-icon="false">
            注入的事件和真实输入走完全相同的流程（规则层 → LLM → 朗读），会产生真实的 LLM/语音费用。
          </n-alert>
          <n-select v-model:value="injectType" :options="injectOptions" placeholder="选择事件类型" style="width: 200px" />
          <template v-if="isText">
            <n-input v-model:value="injectText" type="textarea" :maxlength="500" show-count :autosize="{ minRows: 2, maxRows: 5 }" placeholder="事件文本" />
            <n-space v-if="injectType === 'voice.utterance'" align="center">
              <span>时长（秒）</span>
              <n-input-number v-model:value="injectDuration" :min="0" :max="600" style="width: 120px" />
            </n-space>
          </template>
          <n-radio-group v-else-if="injectType === 'pet.interaction'" v-model:value="injectArea">
            <n-radio-button value="head">head</n-radio-button>
            <n-radio-button value="body">body</n-radio-button>
          </n-radio-group>
          <div>
            <n-button type="primary" :disabled="!canSend" :loading="sending" @click="send">发送</n-button>
          </div>
        </n-space>
      </n-card>
    </n-space>
  </n-spin>
</template>

<style scoped>
.error {
  font-size: inherit;
}
.row {
  padding: 8px 6px;
  border-bottom: 1px solid rgba(128, 128, 128, 0.2);
}
.line {
  display: flex;
  align-items: center;
  gap: 10px;
}
.time {
  font-variant-numeric: tabular-nums;
  opacity: 0.7;
}
.id {
  font-family: Consolas, 'Courier New', monospace;
  font-size: 0.85em;
  opacity: 0.75;
  cursor: pointer;
}
.summary {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.result {
  margin-top: 4px;
  padding-left: 4px;
  display: flex;
  align-items: center;
  gap: 6px;
}
.detail {
  margin: 6px 0 0;
  padding: 8px;
  font-family: Consolas, 'Courier New', monospace;
  font-size: 0.85em;
  background: rgba(128, 128, 128, 0.12);
  border-radius: 4px;
  white-space: pre-wrap;
  word-break: break-all;
}
.hl {
  animation: flash 2.5s ease-out;
}
@keyframes flash {
  0% {
    background: rgba(24, 160, 88, 0.35);
  }
  100% {
    background: transparent;
  }
}
</style>
