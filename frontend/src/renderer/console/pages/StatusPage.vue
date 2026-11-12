<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive } from 'vue'
import {
  NAlert,
  NButton,
  NCard,
  NCollapse,
  NCollapseItem,
  NColorPicker,
  NEmpty,
  NForm,
  NFormItem,
  NGrid,
  NGridItem,
  NInput,
  NInputNumber,
  NPopconfirm,
  NSpace,
  NSpin,
  NSwitch,
  NTag,
  useMessage
} from 'naive-ui'
import type { StatusStat } from '../../../shared/backend'
import BackendOffline from '../components/BackendOffline.vue'
import PageHead from '../components/PageHead.vue'
import SaveBar from '../components/SaveBar.vue'
import StatusBar from '../components/StatusBar.vue'
import { errorText } from '../store'
import { useTask } from '../task'
import {
  AFFECTION_ID,
  dirty,
  formError,
  loadStatus,
  newStat,
  refreshLive,
  revertStatus,
  saveEnabled,
  saveStatus,
  setValue,
  statError,
  status
} from '../status'

const REFRESH_MS = 3000
const SWATCHES = ['#ff7eb6', '#8c6cff', '#4aa8ff', '#31c48d', '#f0a020', '#f2647a', '#20b8c8']

const message = useMessage()
const { busy: saving, run: runSave } = useTask()
// Values typed into "改成" per stat id.
const manual = reactive<Record<string, number | null>>({})
let timer: number | null = null

onMounted(() => {
  // Re-read unless there are unsaved edits, so hand edits to status.yaml aren't silently overwritten.
  if (!status.loaded || !dirty.value) void loadStatus()
  else void refreshLive()
  timer = window.setInterval(() => void refreshLive(), REFRESH_MS)
})
onUnmounted(() => {
  if (timer !== null) clearInterval(timer)
})

// Stable keys for rows that can be removed or re-sorted (the id field itself is editable).
const uids = new WeakMap<object, number>()
let nextUid = 1
function uid(o: object): number {
  let n = uids.get(o)
  if (n === undefined) uids.set(o, (n = nextUid++))
  return n
}

const liveOf = (id: string) => status.live.find((x) => x.id === id)
// The bars show the saved stats with their live numbers (unsaved edits don't move them).
const bars = computed(() =>
  status.saved.stats.map((s) => {
    const live = liveOf(s.id)
    const left = s.daily_up > 0 ? Math.max(0, +(s.daily_up - (live?.gained_today ?? 0)).toFixed(2)) : null
    return {
      stat: s,
      value: live?.value ?? s.initial,
      tier: live?.tier ?? null,
      ticks: s.tiers.map((t) => t.min),
      note: left === null ? '' : left > 0 ? `今天还能涨 ${left}` : '今天已经涨到上限了，明天再来'
    }
  })
)

const save = (): Promise<boolean> => runSave(saveStatus, '已保存，下一句对话生效')

// The switch is the one setting people expect to act at once. Only the switch is saved: other edits wait for 保存.
const { busy: toggling, run: runToggle } = useTask()
const toggle = (on: boolean): Promise<boolean> => runToggle(() => saveEnabled(on), on ? '状态栏已开启' : '状态栏已关闭')

async function applyValue(id: string, value: number | null): Promise<void> {
  try {
    await setValue(id, value)
    manual[id] = null
    message.success(value === null ? '已重置为初始值' : '已修改')
  } catch (e) {
    message.error(errorText(e))
  }
}

function addStat(): void {
  status.form.stats.push(newStat())
}
function removeStat(i: number): void {
  status.form.stats.splice(i, 1)
}
function addTier(s: StatusStat): void {
  const top = s.tiers.length ? Math.max(...s.tiers.map((t) => t.min)) : s.min
  const step = +((s.max - s.min) / 10).toFixed(2) || 1
  s.tiers.push({ min: s.tiers.length ? Math.min(s.max, +(top + step).toFixed(2)) : s.min, name: `阶段 ${s.tiers.length + 1}`, prompt: '' })
}
const sortTiers = (s: StatusStat): void => void s.tiers.sort((a, b) => a.min - b.min)
/** "10 ~ 20" for the tier at index i of a sorted list. */
function tierRange(s: StatusStat, i: number): string {
  const sorted = [...s.tiers].sort((a, b) => a.min - b.min)
  const at = sorted.indexOf(s.tiers[i])
  const next = sorted[at + 1]
  const from = at === 0 ? Math.min(s.min, s.tiers[i].min) : s.tiers[i].min
  return next ? `${from} ~ ${next.min}` : `${from} 以上`
}
const time = (ts: number): string => new Date(ts * 1000).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
const signed = (v: number): string => `${v > 0 ? '+' : ''}${+v.toFixed(2)}`
const colorOf = (id: string): string => status.saved.stats.find((s) => s.id === id)?.color ?? '#999999'
</script>

<template>
  <page-head title="状态">
    <n-tag v-if="status.loaded && !status.offline" :type="status.saved.enabled ? 'success' : 'default'" size="small" round :bordered="false">
      {{ status.saved.enabled ? '状态栏已开启' : '状态栏已关闭' }}
    </n-tag>
    <n-switch v-if="status.loaded && !status.offline" class="master" :value="status.saved.enabled" :loading="toggling" @update:value="toggle" />
  </page-head>
  <n-spin :show="status.loading && !status.loaded">
    <backend-offline v-if="status.offline || (!status.loaded && status.error)" :error="status.error" :loading="status.loading" @retry="loadStatus" />
    <n-space v-else-if="status.loaded" vertical :size="20">
      <span class="hint">
        开启后，提示词里会多出「状态值」和「当前态度」两段：模型只说「+0.3」「-1」这样的变化量，数值由程序来加、来限幅；值变化时桌宠头部右侧会飘出提示和原因。
        关闭后不加任何提示词，数值也不再变化（已有的数值保留）。
      </span>

      <n-card class="status-card" :class="{ off: !status.saved.enabled }">
        <n-space vertical :size="22">
          <div v-for="b in bars" :key="b.stat.id" class="row" :class="{ main: b.stat.id === AFFECTION_ID }">
            <status-bar
              :id="b.stat.id"
              :class="{ 'sb-main': b.stat.id === AFFECTION_ID }"
              :name="b.stat.name"
              :value="b.value"
              :min="b.stat.min"
              :max="b.stat.max"
              :color="b.stat.color"
              :tier="b.tier"
              :ticks="b.ticks"
              :note="b.note"
            />
            <div class="adjust">
              <n-input-number
                v-model:value="manual[b.stat.id]"
                size="tiny"
                :min="b.stat.min"
                :max="b.stat.max"
                :show-button="false"
                placeholder="改成…"
                style="width: 84px"
              />
              <n-button size="tiny" :disabled="manual[b.stat.id] == null" @click="applyValue(b.stat.id, manual[b.stat.id] ?? null)">设置</n-button>
              <n-popconfirm @positive-click="applyValue(b.stat.id, null)">
                <template #trigger><n-button size="tiny" quaternary>重置</n-button></template>
                把「{{ b.stat.name }}」重置为初始值 {{ b.stat.initial }}？
              </n-popconfirm>
            </div>
          </div>
        </n-space>
      </n-card>

      <n-card title="最近的变化" size="small">
        <n-empty v-if="status.recent.length === 0" size="small" description="还没有变化。开启后和它聊聊天吧" />
        <div v-else class="recent">
          <div v-for="(r, i) in status.recent.slice(0, 12)" :key="`${r.ts}-${r.id}-${i}`" class="recent-row">
            <span class="r-time">{{ time(r.ts) }}</span>
            <span class="r-name">{{ r.name }}</span>
            <b class="r-delta" :style="{ color: r.delta > 0 ? colorOf(r.id) : undefined }" :class="{ down: r.delta < 0 }">{{ signed(r.delta) }}</b>
            <span class="r-value">→ {{ +r.value.toFixed(2) }}</span>
            <span class="r-reason" :title="r.reason">{{ r.reason || '（没说原因）' }}</span>
            <n-tag v-if="r.tier" size="tiny" round :bordered="false" type="warning">进入「{{ r.tier }}」</n-tag>
          </div>
        </div>
      </n-card>

      <n-card title="状态值设置">
        <n-alert v-if="formError" type="error" :show-icon="false" style="margin-bottom: 12px">{{ formError }}</n-alert>
        <n-collapse accordion :default-expanded-names="[]">
          <n-collapse-item v-for="(s, i) in status.form.stats" :key="uid(s)" :name="uid(s)">
            <template #header>
              <span class="swatch" :style="{ background: s.color }" />
              <span class="stat-title">{{ s.name || s.id }}</span>
              <span class="stat-sub">{{ s.id }} · {{ s.min }} ~ {{ s.max }} · {{ s.tiers.length }} 个区间</span>
              <n-tag v-if="statError(s, status.form.stats)" size="tiny" type="error" :bordered="false" style="margin-left: 8px">有问题</n-tag>
            </template>
            <template #header-extra>
              <n-popconfirm v-if="s.id !== AFFECTION_ID" @positive-click="removeStat(i)">
                <template #trigger><n-button size="tiny" type="error" quaternary @click.stop>删除</n-button></template>
                删除「{{ s.name }}」？保存后生效。
              </n-popconfirm>
              <n-tag v-else size="tiny" :bordered="false">内置</n-tag>
            </template>

            <n-form label-placement="top" size="small">
              <n-grid :cols="3" :x-gap="12">
                <n-grid-item>
                  <n-form-item label="名称"><n-input v-model:value="s.name" :maxlength="12" placeholder="例：心情" /></n-form-item>
                </n-grid-item>
                <n-grid-item>
                  <n-form-item label="id（模型用它指代这个值）">
                    <n-input v-model:value="s.id" :maxlength="24" :disabled="s.id === AFFECTION_ID" placeholder="例：mood" />
                  </n-form-item>
                </n-grid-item>
                <n-grid-item>
                  <n-form-item label="进度条颜色">
                    <n-color-picker v-model:value="s.color" :modes="['hex']" :show-alpha="false" :swatches="SWATCHES" />
                  </n-form-item>
                </n-grid-item>
              </n-grid>
              <n-form-item label="含义（这个值代表什么）">
                <n-input v-model:value="s.meaning" :maxlength="200" show-count placeholder="例：你现在的心情好坏" />
              </n-form-item>
              <n-form-item label="增减规则（什么时候加、什么时候减、一般加减多少）">
                <n-input v-model:value="s.rule" type="textarea" :maxlength="300" show-count :autosize="{ minRows: 2, maxRows: 5 }" placeholder="例：主人陪你聊开心的事 +1 到 +3；被凶、被无视 -2 到 -5；平常不变。" />
              </n-form-item>
              <n-grid :cols="3" :x-gap="12">
                <n-grid-item><n-form-item label="最小值（可以是负数）"><n-input-number v-model:value="s.min" :step="1" /></n-form-item></n-grid-item>
                <n-grid-item><n-form-item label="最大值"><n-input-number v-model:value="s.max" :step="1" /></n-form-item></n-grid-item>
                <n-grid-item><n-form-item label="初始值"><n-input-number v-model:value="s.initial" :step="1" /></n-form-item></n-grid-item>
                <n-grid-item><n-form-item label="一次最多加"><n-input-number v-model:value="s.max_up" :min="0" :step="0.1" /></n-form-item></n-grid-item>
                <n-grid-item><n-form-item label="一次最多减"><n-input-number v-model:value="s.max_down" :min="0" :step="0.1" /></n-form-item></n-grid-item>
                <n-grid-item><n-form-item label="每天最多涨（0 = 不限）"><n-input-number v-model:value="s.daily_up" :min="0" :step="1" /></n-form-item></n-grid-item>
              </n-grid>
              <div class="hint">模型要求的变化超过「一次最多」时会被截到上限；「每天最多涨」只限制上升，下降不受限。</div>

              <div class="tiers-head">
                <b>区间与态度</b>
                <span class="hint">值落在某个区间时，把那一段「附加提示词」加进提示词，用来改变它对你的态度。不需要可以不填。</span>
              </div>
              <div v-for="(t, k) in s.tiers" :key="uid(t)" class="tier" :style="{ '--t-color': s.color }">
                <div class="tier-top">
                  <span class="tier-from">从</span>
                  <n-input-number v-model:value="t.min" size="tiny" :show-button="false" style="width: 76px" @blur="sortTiers(s)" />
                  <n-input v-model:value="t.name" size="tiny" :maxlength="12" placeholder="阶段名" style="width: 120px" />
                  <span class="tier-range">{{ tierRange(s, k) }}</span>
                  <n-button size="tiny" quaternary type="error" style="margin-left: auto" @click="s.tiers.splice(k, 1)">删除</n-button>
                </div>
                <n-input v-model:value="t.prompt" type="textarea" size="small" :maxlength="500" :autosize="{ minRows: 1, maxRows: 5 }" placeholder="这个阶段它对你的态度，例：你和主人已经很熟了，说话随意，会撒娇。" />
              </div>
              <n-button size="small" dashed block :disabled="s.tiers.length >= status.maxTiers" @click="addTier(s)">+ 添加区间</n-button>
            </n-form>
          </n-collapse-item>
        </n-collapse>
        <n-button dashed block style="margin-top: 14px" :disabled="status.form.stats.length >= status.maxStats" @click="addStat">+ 添加自定义状态值</n-button>
      </n-card>
    </n-space>
  </n-spin>
  <save-bar
    v-if="status.loaded && !status.offline"
    :dirty="dirty"
    :saving="saving"
    :problem="formError"
    hint="进度条的样子可以在「界面」页用自定义 CSS 修改"
    @save="save"
    @revert="revertStatus"
  />
</template>

<style scoped>
.master {
  margin-left: auto;
}
.hint {
  line-height: 1.6;
}
.status-card {
  background: linear-gradient(135deg, rgba(255, 126, 182, 0.1), rgba(140, 108, 255, 0.07) 60%, transparent);
  transition: filter 0.3s, opacity 0.3s;
}
.status-card.off {
  filter: grayscale(0.85);
  opacity: 0.7;
}
.adjust {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 6px;
  margin-top: 6px;
  opacity: 0.75;
  transition: opacity 0.15s;
}
.row:hover .adjust,
.adjust:focus-within {
  opacity: 1;
}
.recent-row {
  display: flex;
  align-items: baseline;
  gap: 10px;
  padding: 5px 0;
  font-size: 0.93em;
  border-bottom: 1px dashed rgba(128, 128, 128, 0.18);
}
.recent-row:last-child {
  border-bottom: 0;
}
.r-time {
  opacity: 0.55;
  font-variant-numeric: tabular-nums;
}
.r-name {
  font-weight: 600;
}
.r-delta {
  min-width: 3em;
  font-variant-numeric: tabular-nums;
}
.r-delta.down {
  color: #7d8597;
}
.r-value {
  opacity: 0.6;
  font-variant-numeric: tabular-nums;
}
.r-reason {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.swatch {
  display: inline-block;
  width: 10px;
  height: 10px;
  margin-right: 8px;
  border-radius: 50%;
}
.stat-title {
  font-weight: 600;
}
.stat-sub {
  margin-left: 10px;
  font-size: 0.85em;
  opacity: 0.55;
}
.tiers-head {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin: 18px 0 10px;
}
.tier {
  margin-bottom: 8px;
  padding: 8px 10px;
  border-left: 3px solid var(--t-color);
  border-radius: 6px;
  background: color-mix(in srgb, var(--t-color) 7%, transparent);
}
.tier-top {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.tier-from,
.tier-range {
  font-size: 0.85em;
  opacity: 0.6;
}
</style>
