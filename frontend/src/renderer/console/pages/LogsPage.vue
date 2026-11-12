<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { NAlert, NButton, NInput, NRadioButton, NRadioGroup, NSelect, NSpace, NTag, useMessage, type SelectOption } from 'naive-ui'
import type { LogEntry } from '../../../shared/backend'
import PageHead from '../components/PageHead.vue'
import { clearLocal, filtersChanged, logs, openFolder, startLogs, stopLogs, type SourceFilter } from '../logs'
import { countLevels, formatTime, levelTagType, type LogRow } from '../logs-util'

const message = useMessage()

const levelOptions: SelectOption[] = (['DEBUG', 'INFO', 'WARNING', 'ERROR'] as const).map((l) => ({ label: l, value: l }))
const stats = computed(() => countLevels(logs.rows))

const scroller = ref<HTMLElement | null>(null)
const atBottom = ref(true)
const expanded = ref<Record<string, boolean>>({})

function onScroll(): void {
  const el = scroller.value
  if (!el) return
  atBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < 24
}

function scrollToBottom(): void {
  const el = scroller.value
  if (el) el.scrollTop = el.scrollHeight
}

// Follow new lines only while the user is at the bottom.
watch(
  () => logs.rows,
  async () => {
    const stick = atBottom.value
    await nextTick()
    if (stick) scrollToBottom()
  }
)

onMounted(() => {
  startLogs()
})
onUnmounted(() => {
  stopLogs()
})

function onSource(v: SourceFilter): void {
  logs.source = v
  filtersChanged()
}
function onLevel(v: LogEntry['level']): void {
  logs.level = v
  filtersChanged()
}
function onQ(v: string): void {
  logs.q = v ?? ''
  filtersChanged(true)
}
function onEventId(v: string): void {
  logs.eventId = v ?? ''
  filtersChanged(true)
}
function pickEvent(id: string): void {
  logs.eventId = id
  filtersChanged()
}
function pickLevel(l: LogEntry['level']): void {
  logs.level = l
  filtersChanged()
}
function clearScreen(): void {
  clearLocal()
  expanded.value = {}
}

async function copy(text: string, okText: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text)
    message.success(okText)
  } catch (e) {
    message.error(`复制失败：${e instanceof Error ? e.message : String(e)}`)
  }
}

function copyLocation(r: LogRow): Promise<void> {
  return copy(`${r.file}:${r.line}`, '已复制位置')
}

function sourceText(r: LogRow): string {
  return r.source === 'backend' ? '后端' : `前端·${r.logger}`
}

function toggle(key: string): void {
  expanded.value = { ...expanded.value, [key]: !expanded.value[key] }
}

async function openDir(): Promise<void> {
  const err = await openFolder()
  if (err) message.error(err, { duration: 6000 })
}
</script>

<template>
  <page-head title="日志" :gap="12">
    <div class="spacer" />
    <n-space :size="8" align="center">
      <n-tag size="small" round :type="stats.error ? 'error' : 'default'" class="stat" @click="pickLevel('ERROR')">
        ERROR {{ stats.error }}
      </n-tag>
      <n-tag size="small" round :type="stats.warning ? 'warning' : 'default'" class="stat" @click="pickLevel('WARNING')">
        WARNING {{ stats.warning }}
      </n-tag>
    </n-space>
  </page-head>

  <n-alert v-if="logs.backendOffline && logs.source !== 'frontend'" type="warning" class="offline">
    后端未运行，只显示前端日志
  </n-alert>

  <n-space class="toolbar" align="center" :size="10">
    <n-radio-group :value="logs.source" size="small" @update:value="onSource">
      <n-radio-button value="all">全部</n-radio-button>
      <n-radio-button value="backend">后端</n-radio-button>
      <n-radio-button value="frontend">前端</n-radio-button>
    </n-radio-group>
    <n-select :value="logs.level" :options="levelOptions" size="small" style="width: 120px" @update:value="onLevel" />
    <n-input :value="logs.q" size="small" clearable placeholder="关键字" style="width: 180px" @update:value="onQ" />
    <n-input :value="logs.eventId" size="small" clearable placeholder="事件 ID" style="width: 200px" @update:value="onEventId" />
    <n-button size="small" @click="logs.paused = !logs.paused">{{ logs.paused ? '继续' : '暂停' }}</n-button>
    <n-button size="small" @click="clearScreen">清屏</n-button>
    <n-button size="small" @click="openDir">打开日志文件夹</n-button>
  </n-space>

  <div ref="scroller" class="list" @scroll="onScroll">
    <div v-if="logs.rows.length === 0" class="empty">{{ logs.loading ? '加载中…' : '没有符合条件的日志' }}</div>
    <div v-for="r in logs.rows" :key="r.key" class="row" :class="{ err: r.level === 'ERROR' || r.level === 'CRITICAL' }">
      <div class="line">
        <span class="time">{{ formatTime(r.ts) }}</span>
        <n-tag size="tiny" :type="levelTagType(r.level)" :bordered="false" class="lvl">{{ r.level }}</n-tag>
        <span class="src">{{ sourceText(r) }}</span>
        <span class="logger">{{ r.logger }}</span>
        <span class="loc" title="点击复制位置" @click="copyLocation(r)">{{ r.file }}:{{ r.line }}（{{ r.func }}）</span>
        <span class="msg">{{ r.msg }}</span>
        <a v-if="r.event_id" class="evt" href="#" title="只看这个事件" @click.prevent="pickEvent(r.event_id)">{{ r.event_id }}</a>
        <n-button v-if="r.exc" size="tiny" quaternary type="error" @click="toggle(r.key)">
          {{ expanded[r.key] ? '收起' : '调用栈' }}
        </n-button>
      </div>
      <div v-if="r.exc && expanded[r.key]" class="exc">
        <n-button size="tiny" class="copy" @click="copy(r.exc, '已复制调用栈')">复制</n-button>
        <pre>{{ r.exc }}</pre>
      </div>
    </div>
  </div>
</template>

<style scoped>
.spacer {
  flex: 1;
}
.stat {
  cursor: pointer;
}
.offline {
  margin-bottom: 10px;
}
.toolbar {
  margin-bottom: 10px;
}
.list {
  height: calc(100vh - 210px);
  min-height: 240px;
  overflow: auto;
  border: 1px solid rgba(128, 128, 128, 0.3);
  border-radius: 4px;
  font-size: 0.88em;
}
.empty {
  padding: 24px;
  text-align: center;
  opacity: 0.6;
}
.row {
  padding: 3px 8px;
  border-bottom: 1px solid rgba(128, 128, 128, 0.12);
}
.row.err {
  background: rgba(208, 48, 80, 0.07);
}
.line {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 4px 10px;
}
.time,
.loc,
.exc pre {
  font-family: Consolas, 'Courier New', monospace;
}
.time {
  opacity: 0.7;
}
.src,
.logger {
  opacity: 0.7;
}
.loc {
  cursor: pointer;
  color: #2080f0;
}
.loc:hover {
  text-decoration: underline;
}
.msg {
  white-space: pre-wrap;
  word-break: break-word;
}
.evt {
  font-size: 0.85em;
  color: #18a058;
}
.exc {
  position: relative;
  margin: 4px 0 4px 8px;
  padding: 6px 8px;
  background: rgba(128, 128, 128, 0.12);
  border-radius: 4px;
}
.exc pre {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-all;
}
.copy {
  position: absolute;
  top: 4px;
  right: 4px;
}
</style>
