<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { NAlert, NButton, NCheckbox, NDropdown, NEmpty, NInput, NInputNumber, NModal, NPopconfirm, NSpin, NTag, useMessage } from 'naive-ui'
import type { ChatMessage, HistoryDay, HistoryDeleteRequest } from '../../../shared/backend'
import BackendOffline from '../components/BackendOffline.vue'
import PageHead from '../components/PageHead.vue'
import {
  PAGE_SIZE,
  dayLabel,
  deleteHistory,
  fetchHistory,
  fetchHistoryDays,
  fetchSummarizedCount,
  fmtClock,
  groupByYear,
  highlightSegments,
  withSeparators
} from '../history'
import { errorText } from '../store'

const message = useMessage()

/** Oldest first. */
const messages = ref<ChatMessage[]>([])
const hasMore = ref(false)
const days = ref<HistoryDay[]>([])
const selectedDate = ref<string | null>(null)
const search = ref('')
/** The search term the current list was loaded with (drives highlighting). */
const activeQuery = ref('')

const loading = ref(false)
const loadingMore = ref(false)
const loaded = ref(false)
const offline = ref(false)
const error = ref<string | null>(null)

const scroller = ref<HTMLElement | null>(null)
const hoverId = ref<number | null>(null)
// Keeps the action bar mounted while its confirm popover is open (the popover lives outside .msg).
const confirmingId = ref<number | null>(null)

const confirm = ref<null | { kind: 'day' | 'all' | 'selected'; count: number }>(null)

// Multi-select: pick messages (of the loaded ones) and delete them together.
const DELETE_CHUNK = 1000
const selecting = ref(false)
const selected = ref<number[]>([])
const selectedSet = computed(() => new Set(selected.value))

// Clean up already-summarized messages, keeping the newest N.
const cleanup = ref<null | { keep: number; count: number | null; busy: boolean }>(null)
let countSeq = 0
const confirmBusy = ref(false)

let debounce: ReturnType<typeof setTimeout> | null = null
/** Bumped on every reset so late responses of an older query are dropped. */
let generation = 0

const groups = computed(() => groupByYear(days.value))
const totalCount = computed(() => days.value.reduce((sum, d) => sum + d.count, 0))
const items = computed(() => withSeparators(messages.value))
const dayCount = computed(() => days.value.find((d) => d.date === selectedDate.value)?.count ?? 0)
const menuOptions = computed(() => [
  { label: '删除这一天', key: 'day', disabled: selectedDate.value === null },
  { label: '清空全部聊天记录', key: 'all' }
])
const sumOptions = [{ label: '清理已总结的记录…', key: 'cleanup' }]
const confirmText = computed(() => {
  if (!confirm.value) return ''
  const what = confirm.value.kind === 'day' ? `${selectedDate.value ?? ''} 的` : confirm.value.kind === 'selected' ? '所选的' : '全部'
  return `将删除${what} ${confirm.value.count} 条聊天记录，无法恢复。已总结出的长期记忆不会被删除，可以在「记忆」页单独管理。`
})

function applyFailure(res: { offline: boolean; error: string }): void {
  offline.value = res.offline
  error.value = res.error
}

async function loadDays(): Promise<void> {
  try {
    const res = await fetchHistoryDays()
    if (res.ok) days.value = res.data
    else applyFailure(res)
  } catch (e) {
    error.value = errorText(e)
  }
}

async function scrollToBottom(): Promise<void> {
  await nextTick()
  const el = scroller.value
  if (el) el.scrollTop = el.scrollHeight
}

/** Reloads the first page for the current filters and jumps to the newest message. */
async function reset(): Promise<void> {
  const gen = ++generation
  loading.value = true
  loadingMore.value = false
  const q = search.value.trim()
  try {
    const res = await fetchHistory({ limit: PAGE_SIZE, q, date: selectedDate.value })
    if (gen !== generation) return
    if (!res.ok) {
      applyFailure(res)
      return
    }
    offline.value = false
    error.value = null
    activeQuery.value = q
    messages.value = [...res.data.messages].reverse()
    hasMore.value = res.data.has_more
    loaded.value = true
    await scrollToBottom()
  } catch (e) {
    if (gen === generation) error.value = errorText(e)
  } finally {
    if (gen === generation) loading.value = false
  }
}

async function loadMore(): Promise<void> {
  if (loading.value || loadingMore.value || !hasMore.value || messages.value.length === 0) return
  const gen = generation
  loadingMore.value = true
  const el = scroller.value
  const prevHeight = el?.scrollHeight ?? 0
  const prevTop = el?.scrollTop ?? 0
  try {
    const res = await fetchHistory({
      beforeId: messages.value[0].id,
      limit: PAGE_SIZE,
      q: activeQuery.value,
      date: selectedDate.value
    })
    if (gen !== generation) return
    if (!res.ok) {
      message.error(res.error)
      return
    }
    messages.value = [...[...res.data.messages].reverse(), ...messages.value]
    hasMore.value = res.data.has_more
    await nextTick()
    if (el) el.scrollTop = prevTop + (el.scrollHeight - prevHeight)
  } catch (e) {
    message.error(errorText(e))
  } finally {
    if (gen === generation) loadingMore.value = false
  }
}

function onScroll(): void {
  const el = scroller.value
  if (el && el.scrollTop < 40) void loadMore()
}

async function refresh(): Promise<void> {
  await Promise.all([loadDays(), reset()])
}

function selectDate(date: string | null): void {
  if (selectedDate.value === date) return
  selectedDate.value = date
}

watch(selectedDate, () => void reset())
watch(search, () => {
  if (debounce !== null) clearTimeout(debounce)
  debounce = setTimeout(() => void reset(), 300)
})

onMounted(() => {
  void refresh()
})
onUnmounted(() => {
  if (debounce !== null) clearTimeout(debounce)
  generation++
})

async function runDelete(body: HistoryDeleteRequest): Promise<number | null> {
  try {
    const res = await deleteHistory(body)
    if (!res.ok) {
      message.error(res.error, { duration: 6000 })
      return null
    }
    message.success(`已删除 ${res.data.deleted} 条`)
    return res.data.deleted
  } catch (e) {
    message.error(errorText(e), { duration: 6000 })
    return null
  }
}

async function deleteOne(id: number): Promise<void> {
  const n = await runDelete({ ids: [id] })
  if (n === null) return
  messages.value = messages.value.filter((m) => m.id !== id)
  await loadDays()
  if (messages.value.length === 0) await reset()
}

function toggleSelecting(): void {
  selecting.value = !selecting.value
  selected.value = []
}

function toggleSelected(id: number): void {
  selected.value = selectedSet.value.has(id) ? selected.value.filter((x) => x !== id) : [...selected.value, id]
}

function selectAllLoaded(): void {
  selected.value = messages.value.map((m) => m.id)
}

async function refreshCleanupCount(): Promise<void> {
  const c = cleanup.value
  if (!c) return
  const seq = ++countSeq
  const res = await fetchSummarizedCount(c.keep ?? 0).catch(() => null)
  if (seq !== countSeq || !cleanup.value) return
  cleanup.value.count = res && res.ok ? res.data.deleted : null
}

function openCleanup(): void {
  cleanup.value = { keep: 20, count: null, busy: false }
  void refreshCleanupCount()
}

watch(
  () => cleanup.value?.keep,
  () => {
    if (cleanup.value) cleanup.value.count = null
    void refreshCleanupCount()
  }
)

async function doCleanup(): Promise<void> {
  const c = cleanup.value
  if (!c) return
  c.busy = true
  try {
    const n = await runDelete({ summarized: true, keep_recent: c.keep ?? 0 })
    if (n === null) return
    cleanup.value = null
    selected.value = []
    await loadDays()
    if (selectedDate.value && !days.value.some((d) => d.date === selectedDate.value)) selectedDate.value = null
    else await reset()
  } finally {
    if (cleanup.value) cleanup.value.busy = false
  }
}

function onMenu(key: string): void {
  if (key === 'cleanup') openCleanup()
  else if (key === 'day' && selectedDate.value !== null) confirm.value = { kind: 'day', count: dayCount.value }
  else if (key === 'all') confirm.value = { kind: 'all', count: totalCount.value }
}

async function doConfirm(): Promise<void> {
  const c = confirm.value
  if (!c) return
  confirmBusy.value = true
  try {
    if (c.kind === 'selected') {
      const ids = [...selected.value]
      let total = 0
      for (let i = 0; i < ids.length; i += DELETE_CHUNK) {
        const n = await runDelete({ ids: ids.slice(i, i + DELETE_CHUNK) })
        if (n === null) break
        total += n
      }
      confirm.value = null
      selected.value = []
      await loadDays()
      await reset()
      if (total === 0) message.warning('没有删除任何记录')
      return
    }
    const body: HistoryDeleteRequest = c.kind === 'day' && selectedDate.value ? { date: selectedDate.value } : { all: true }
    const n = await runDelete(body)
    if (n === null) return
    confirm.value = null
    await loadDays()
    if (c.kind === 'all') selectedDate.value = null
    else if (!days.value.some((d) => d.date === selectedDate.value)) selectedDate.value = null
    else await reset()
    if (c.kind === 'all') await reset()
  } finally {
    confirmBusy.value = false
  }
}

async function copyEventId(id: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(id)
    message.success('已复制')
  } catch (e) {
    message.error(errorText(e))
  }
}

function sourceTag(m: ChatMessage): string | null {
  if (m.role !== 'user') return null
  if (m.source === 'voice.utterance') return '语音'
  if (m.source === 'pet.interaction') return '互动'
  return null
}
</script>

<template>
  <page-head title="聊天记录" :gap="16" />
  <backend-offline v-if="offline" :error="error" :loading="loading" @retry="refresh" />
  <n-alert v-else-if="!loaded && error" type="error" :show-icon="false">
    <p class="err">{{ error }}</p>
    <n-button size="small" :loading="loading" @click="refresh">重试</n-button>
  </n-alert>
  <div v-else class="layout">
    <aside class="days">
      <div class="day" :class="{ active: selectedDate === null }" @click="selectDate(null)">全部</div>
      <template v-for="g in groups" :key="g.year">
        <div class="year">{{ g.year }}</div>
        <div v-for="d in g.days" :key="d.date" class="day" :class="{ active: selectedDate === d.date }" @click="selectDate(d.date)">
          {{ dayLabel(d.date) }}（{{ d.count }}）
        </div>
      </template>
    </aside>

    <section class="main">
      <div class="toolbar">
        <n-input v-model:value="search" clearable placeholder="搜索聊天内容" style="max-width: 280px" />
        <n-button :loading="loading" @click="refresh">刷新</n-button>
        <n-button :type="selecting ? 'primary' : 'default'" @click="toggleSelecting">{{ selecting ? '退出多选' : '多选' }}</n-button>
        <n-dropdown trigger="click" :options="[...sumOptions, ...menuOptions]" @select="onMenu">
          <n-button type="error" secondary>清理 / 删除</n-button>
        </n-dropdown>
        <span v-if="error" class="err">{{ error }}</span>
      </div>
      <div v-if="selecting" class="selbar">
        <span>已选 {{ selected.length }} 条</span>
        <n-button size="small" @click="selectAllLoaded">全选已加载的 {{ messages.length }} 条</n-button>
        <n-button size="small" :disabled="selected.length === 0" @click="selected = []">取消选择</n-button>
        <n-button size="small" type="error" :disabled="selected.length === 0" @click="confirm = { kind: 'selected', count: selected.length }">删除所选</n-button>
      </div>

      <n-spin :show="loading && !loadingMore" class="spin">
        <div ref="scroller" class="chat" @scroll="onScroll">
          <n-empty v-if="loaded && messages.length === 0" class="empty" :description="activeQuery || selectedDate ? '没有符合条件的记录。' : '还没有聊天记录。和桌宠聊几句就会出现在这里。'" />
          <template v-else>
            <div v-if="hasMore" class="more">
              <n-button size="small" :loading="loadingMore" @click="loadMore">加载更早的记录</n-button>
            </div>
            <template v-for="it in items" :key="it.key">
              <div v-if="it.separator" class="sep"><span>{{ it.separator }}</span></div>
              <div
                class="msg"
                :class="[it.msg.role, { selecting, picked: selectedSet.has(it.msg.id) }]"
                @mouseenter="hoverId = it.msg.id"
                @mouseleave="hoverId = null"
                @click="selecting && toggleSelected(it.msg.id)"
              >
                <n-checkbox v-if="selecting" class="pick" :checked="selectedSet.has(it.msg.id)" @click.stop @update:checked="toggleSelected(it.msg.id)" />
                <div class="bubble-wrap">
                  <div v-show="!selecting && (hoverId === it.msg.id || confirmingId === it.msg.id)" class="actions">
                    <n-button v-if="it.msg.event_id" text size="tiny" @click="copyEventId(it.msg.event_id)">复制事件 ID</n-button>
                    <n-popconfirm
                      @positive-click="deleteOne(it.msg.id)"
                      @update:show="(v: boolean) => (confirmingId = v ? it.msg.id : null)"
                    >
                      <template #trigger>
                        <n-button text size="tiny" type="error">删除</n-button>
                      </template>
                      删除这条消息？
                    </n-popconfirm>
                  </div>
                  <div class="bubble">
                    <span v-for="(s, i) in highlightSegments(it.msg.text, activeQuery)" :key="i" :class="{ mark: s.hit }">{{ s.text }}</span>
                  </div>
                  <div class="meta">
                    <span class="time">{{ fmtClock(it.msg.ts) }}</span>
                    <n-tag v-if="it.msg.summarized" size="tiny" :bordered="false">已总结</n-tag>
                    <n-tag v-if="sourceTag(it.msg)" size="tiny" :bordered="false">{{ sourceTag(it.msg) }}</n-tag>
                    <n-tag v-if="it.msg.expression" size="tiny" type="info" :bordered="false">{{ it.msg.expression }}</n-tag>
                    <n-tag v-if="it.msg.motion" size="tiny" type="success" :bordered="false">{{ it.msg.motion }}</n-tag>
                  </div>
                </div>
              </div>
            </template>
          </template>
        </div>
      </n-spin>
    </section>
  </div>

  <n-modal
    :show="confirm !== null"
    preset="dialog"
    type="warning"
    :title="confirm?.kind === 'day' ? '删除这一天的聊天记录' : confirm?.kind === 'selected' ? '删除所选的聊天记录' : '清空全部聊天记录'"
    :content="confirmText"
    positive-text="删除"
    negative-text="取消"
    :loading="confirmBusy"
    :mask-closable="false"
    @positive-click="doConfirm"
    @negative-click="confirm = null"
    @close="confirm = null"
  />

  <n-modal :show="cleanup !== null" preset="card" title="清理已总结的聊天记录" style="width: 460px" :mask-closable="false" @close="cleanup = null">
    <template v-if="cleanup">
      <p class="note">
        只删除「已总结」（已经提炼进长期记忆）的记录，还没总结的一条都不会动。长期记忆本身不受影响，可在「记忆」页单独管理。删除后会压缩数据库文件。
      </p>
      <div class="keep">
        保留最近
        <n-input-number v-model:value="cleanup.keep" :min="0" :max="100000" :step="10" size="small" style="width: 120px" />
        条
      </div>
      <p class="note">
        <template v-if="cleanup.count === null">正在统计…</template>
        <template v-else-if="cleanup.count === 0">没有可清理的记录。</template>
        <template v-else>将删除 <b>{{ cleanup.count }}</b> 条，无法恢复。</template>
      </p>
    </template>
    <template #footer>
      <div class="foot">
        <n-button @click="cleanup = null">取消</n-button>
        <n-button type="error" :loading="cleanup?.busy" :disabled="!cleanup || !cleanup.count" @click="doCleanup">删除</n-button>
      </div>
    </template>
  </n-modal>
</template>

<style scoped>
.err {
  color: #d03050;
  margin: 0 0 6px;
}
.layout {
  display: flex;
  gap: 16px;
  height: calc(100vh - 120px);
  min-height: 320px;
}
.days {
  width: 160px;
  flex: none;
  overflow-y: auto;
  border-right: 1px solid rgba(128, 128, 128, 0.2);
  padding-right: 6px;
}
.year {
  margin: 10px 0 4px 8px;
  font-size: 0.85em;
  font-weight: 600;
  opacity: 0.6;
}
.day {
  padding: 6px 8px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 0.92em;
}
.day:hover {
  background: rgba(128, 128, 128, 0.12);
}
.day.active {
  background: rgba(128, 128, 128, 0.22);
  font-weight: 600;
}
.main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}
.toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}
.selbar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
  padding: 6px 10px;
  border-radius: 6px;
  background: rgba(128, 128, 128, 0.14);
}
.note {
  margin: 0 0 12px;
  line-height: 1.6;
  opacity: 0.85;
}
.keep {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}
.foot {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
.msg.selecting {
  align-items: center;
  gap: 8px;
  cursor: pointer;
}
.msg.picked .bubble {
  outline: 2px solid rgba(140, 108, 255, 0.8);
}
.spin {
  flex: 1;
  min-height: 0;
}
.spin :deep(.n-spin-content) {
  height: 100%;
}
.chat {
  height: 100%;
  overflow-y: auto;
  padding: 8px 12px;
  border: 1px solid rgba(128, 128, 128, 0.2);
  border-radius: 6px;
}
.empty {
  margin-top: 60px;
}
.more {
  text-align: center;
  margin-bottom: 8px;
}
.sep {
  text-align: center;
  margin: 12px 0;
  font-size: 0.8em;
  opacity: 0.6;
}
.sep span {
  padding: 2px 10px;
  border-radius: 10px;
  background: rgba(128, 128, 128, 0.15);
}
.msg {
  display: flex;
  margin: 6px 0;
}
.msg.user {
  justify-content: flex-end;
}
.msg.assistant {
  justify-content: flex-start;
}
.bubble-wrap {
  position: relative;
  max-width: 70%;
  display: flex;
  flex-direction: column;
}
.msg.user .bubble-wrap {
  align-items: flex-end;
}
.bubble {
  padding: 8px 12px;
  border-radius: 12px;
  white-space: pre-wrap;
  word-break: break-word;
  background: rgba(128, 128, 128, 0.16);
}
.msg.user .bubble {
  background: rgba(140, 108, 255, 0.22);
}
.meta {
  margin-top: 3px;
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.78em;
}
.time {
  opacity: 0.55;
  font-variant-numeric: tabular-nums;
}
.actions {
  position: absolute;
  top: -8px;
  right: 4px;
  display: flex;
  gap: 8px;
  padding: 1px 8px;
  border-radius: 4px;
  background: rgba(128, 128, 128, 0.3);
  z-index: 1;
}
.mark {
  background: rgba(255, 200, 0, 0.5);
  border-radius: 2px;
}
</style>
