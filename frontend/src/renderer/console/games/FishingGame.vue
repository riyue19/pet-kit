<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { NAlert, NButton, NEmpty, NInputNumber, NModal, NProgress, NRadioButton, NRadioGroup, NSelect, NSpace, NSwitch, NTabPane, NTabs, NTag, useMessage } from 'naive-ui'
import type { FishRarity, FishingBagItem, FishingCommand, FishingLogEntry, FishingSimulation, FishingSpecies } from '../../../shared/backend'
import { composeFish, composeJunk } from '../../../shared/fish/composer'
import BackendOffline from '../components/BackendOffline.vue'
import { fishing, fishingCommand, loadFishing, saveFishingSettings, simulateFishing, waitText } from '../fishing'
import { petName } from '../games'
import { errorText } from '../store'

defineEmits<{ back: [] }>()

const message = useMessage()
const s = computed(() => fishing.state)

// ---------- refresh + countdown ----------
const remaining = ref(0)
let refreshTimer: number | undefined
let tickTimer: number | undefined

function resetCountdown(): void {
  const st = fishing.state
  // Seconds the line has been in the water; -1 = no line out.
  remaining.value = st && st.enabled && st.cast_ts !== null ? Math.max(0, Math.round(st.now - st.cast_ts)) : -1
}

onMounted(() => {
  void loadFishing()
  refreshTimer = window.setInterval(() => {
    if (fishing.busy || fishing.loading || confirmAsking.value || detailId.value !== null) return
    void loadFishing()
  }, 5000)
  tickTimer = window.setInterval(() => {
    if (remaining.value >= 0) remaining.value += 1
  }, 1000)
})
onBeforeUnmount(() => {
  window.clearInterval(refreshTimer)
  window.clearInterval(tickTimer)
})

const countdownText = computed(() => {
  if (!s.value?.enabled) return '已收竿'
  return remaining.value < 0 ? '正在收线' : `已等待 ${waitText(remaining.value)}`
})

// ---------- confirm dialog + command runner ----------
const confirmAsking = ref(false)
const confirmText = ref('')
let confirmResolve: ((v: boolean) => void) | null = null

function askConfirm(text: string): Promise<boolean> {
  confirmText.value = text.replace(/(确定的话|\/钓鱼)[\s\S]*$/, '').trim() || text
  confirmAsking.value = true
  return new Promise((resolve) => {
    confirmResolve = resolve
  })
}
function answerConfirm(v: boolean): void {
  confirmAsking.value = false
  const r = confirmResolve
  confirmResolve = null
  r?.(v)
}

async function run(cmd: FishingCommand, args: string[] = []): Promise<void> {
  if (fishing.busy) return
  try {
    let res = await fishingCommand(cmd, args, false)
    if (!res.ok && res.need_confirm) {
      if (!(await askConfirm(res.text))) return
      res = await fishingCommand(cmd, args, true)
    }
    if (res.ok) message.success(res.text)
    else message.warning(res.text)
  } catch (e) {
    message.error(errorText(e))
  }
}

const onToggle = (v: boolean): void => void run(v ? 'start' : 'stop')

// ---------- 钓场 ----------
const buyQty = reactive<Record<string, number>>({})
const qtyOf = (id: string): number => buyQty[id] ?? 5
const setQty = (id: string, v: number | null): void => {
  buyQty[id] = v ?? 5
}
const hookPercent = computed(() => Math.round((s.value?.hook_chance ?? 0) * 100))
// The snapshot gives the current place / bait as ids; the page shows their names.
const placeName = computed(() => s.value?.locations.find((l) => l.id === s.value?.location)?.name ?? s.value?.location ?? '')

// ---------- shared SVG caches (plain Maps: only rebuilt when the key changes) ----------
const svgCache = new Map<string, string>()
function cached(key: string, make: () => string): string {
  let v = svgCache.get(key)
  if (v === undefined) {
    v = make()
    svgCache.set(key, v)
  }
  return v
}

const RARITY_TAG: Record<FishRarity, 'default' | 'info' | 'warning' | 'error'> = { common: 'default', uncommon: 'info', rare: 'warning', legendary: 'error' }
const RARITY_RANK: Record<FishRarity, number> = { common: 0, uncommon: 1, rare: 2, legendary: 3 }
const RARITY_LABEL: Record<FishRarity, string> = { common: '普通', uncommon: '少见', rare: '稀有', legendary: '传说' }

// ---------- 背包 ----------
const bagRarity = ref<'all' | FishRarity>('all')
const bagSort = ref<'new' | 'size' | 'price'>('new')
const bagRarityOptions = [
  { label: '全部', value: 'all' },
  { label: '普通', value: 'common' },
  { label: '少见', value: 'uncommon' },
  { label: '稀有', value: 'rare' },
  { label: '传说', value: 'legendary' }
]
const bagSortOptions = [
  { label: '最新', value: 'new' },
  { label: '尺寸', value: 'size' },
  { label: '售价', value: 'price' }
]
const bag = computed<FishingBagItem[]>(() => {
  const list = (s.value?.inventory ?? []).filter((f) => bagRarity.value === 'all' || f.rarity === bagRarity.value)
  const by = bagSort.value
  return [...list].sort((a, b) => (by === 'size' ? b.size_cm - a.size_cm : by === 'price' ? b.price - a.price : b.caught_ts - a.caught_ts || b.id - a.id))
})
const bagIcon = (f: FishingBagItem): string => cached(`bag|${f.id}|${f.template}|${f.patterns.join(',')}|${f.trophy}`, () => composeFish({ template: f.template, patterns: f.patterns, mode: 'icon' }))
const sizeText = (n: number): string => `${Math.round(n * 10) / 10} cm`
// A trophy is a "giant" in the bag.
const bagTotal = computed(() => s.value?.inventory.length ?? 0)

// ---------- 图鉴 ----------
const inkOn = ref<boolean>(
  (() => {
    try {
      const v = localStorage.getItem('fishing.ink')
      if (v !== null) return v === '1'
    } catch {
      /* ignore */
    }
    return !window.matchMedia('(prefers-reduced-motion: reduce)').matches
  })()
)
watch(inkOn, (v) => {
  try {
    localStorage.setItem('fishing.ink', v ? '1' : '0')
  } catch {
    /* ignore */
  }
})

const codexPlace = ref<string>('all')
const codexSort = ref<'default' | 'rarity' | 'time'>('default')
// Each place with its own progress, e.g. 小池塘 3/8.
const placeOptions = computed(() => [
  { label: '全部地点', value: 'all' },
  ...(s.value?.locations ?? []).map((l) => {
    const here = speciesList.value.filter((sp) => sp.location_ids.includes(l.id))
    return { label: `${l.name} ${here.filter((sp) => sp.found).length}/${here.length}`, value: l.id }
  })
])
const codexSortOptions = [
  { label: '默认', value: 'default' },
  { label: '稀有度', value: 'rarity' },
  { label: '发现时间', value: 'time' }
]
const speciesList = computed(() => s.value?.species ?? [])
const codexList = computed<FishingSpecies[]>(() => {
  const list = speciesList.value.filter((sp) => codexPlace.value === 'all' || sp.location_ids.includes(codexPlace.value))
  if (codexSort.value === 'rarity') return [...list].sort((a, b) => (b.found ? RARITY_RANK[b.rarity ?? 'common'] : -1) - (a.found ? RARITY_RANK[a.rarity ?? 'common'] : -1))
  if (codexSort.value === 'time') return [...list].sort((a, b) => (b.found ? (b.first_ts ?? 0) : -1) - (a.found ? (a.first_ts ?? 0) : -1))
  return list
})
const codexSvg = computed<Record<string, string>>(() => {
  const ink = inkOn.value
  const out: Record<string, string> = {}
  for (const sp of speciesList.value) {
    out[sp.id] = cached(`codex|${sp.id}|${sp.found}|${!!sp.trophy}|${ink}`, () =>
      sp.found ? composeFish({ template: sp.template, patterns: sp.patterns, mode: 'stamp', filter: ink }) : composeFish({ template: sp.template, mode: 'silhouette' })
    )
  }
  return out
})
const junkList = computed(() => (s.value?.junk ?? []).map((j) => ({ ...j, svg: cached(`junk|${j.id}`, () => composeJunk(j.id)) })))

const detailId = ref<string | null>(null)
const detail = computed(() => speciesList.value.find((sp) => sp.id === detailId.value && sp.found) ?? null)
const detailOpen = computed({
  get: () => detail.value !== null,
  set: (v: boolean) => {
    if (!v) detailId.value = null
  }
})
function openDetail(sp: FishingSpecies): void {
  if (sp.found) detailId.value = sp.id
}
const pad = (n: number): string => String(n).padStart(2, '0')
function fmtDateTime(ts?: number): string {
  if (!ts) return '—'
  const d = new Date(ts * 1000)
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}
const fmtDate = (ts: number): string => fmtDateTime(ts).slice(0, 10)
const fmtHm = (ts: number): string => fmtDateTime(ts).slice(11)

// ---------- 记录与设置 ----------
const OUTCOME_TEXT: Record<FishingLogEntry['outcome'], string> = { empty: '空军', fish: '钓到', escape: '跑了', junk: '杂物', bottle: '漂流瓶' }
const logRows = computed(() => (s.value?.log ?? []).slice(0, 40))
const logName = (e: FishingLogEntry): string => (e.name ? (e.size_cm ? `${e.name} ${sizeText(e.size_cm)}` : e.name) : '—')

interface Draft {
  waitMin: number
  waitMax: number
  biteSec: number
  budget: number
  animateEmpty: boolean
  hudAnimation: boolean
}
const draft = reactive<Draft>({ waitMin: 5, waitMax: 180, biteSec: 3, budget: 0, animateEmpty: false, hudAnimation: true })
const synced = ref<Draft>({ ...draft })
const sameDraft = (a: Draft, b: Draft): boolean => a.waitMin === b.waitMin && a.waitMax === b.waitMax && a.biteSec === b.biteSec && a.budget === b.budget && a.animateEmpty === b.animateEmpty && a.hudAnimation === b.hudAnimation
const dirty = computed(() => !sameDraft(draft, synced.value))

function syncDraft(): void {
  const st = fishing.state?.settings
  if (!st) return
  const d: Draft = {
    waitMin: Math.round(st.wait_min_sec / 60),
    waitMax: Math.round(st.wait_max_sec / 60),
    biteSec: st.bite_window_ms / 1000,
    budget: st.llm_budget_per_day,
    animateEmpty: st.animate_empty,
    hudAnimation: st.hud_animation
  }
  synced.value = { ...d }
  Object.assign(draft, d)
}
watch(
  () => fishing.state,
  () => {
    resetCountdown()
    if (!dirty.value) syncDraft()
  },
  { immediate: true }
)

async function saveSettings(): Promise<void> {
  const a = draft
  const b = synced.value
  if (a.waitMin > a.waitMax) {
    message.warning('最短等待不能大于最长等待')
    return
  }
  const patch: Partial<{ wait_min_sec: number; wait_max_sec: number; bite_window_ms: number; llm_budget_per_day: number; animate_empty: boolean; hud_animation: boolean }> = {}
  // Both together: the backend checks min ≤ max on what it is sent plus what it has.
  if (a.waitMin !== b.waitMin || a.waitMax !== b.waitMax) {
    patch.wait_min_sec = Math.round(a.waitMin * 60)
    patch.wait_max_sec = Math.round(a.waitMax * 60)
  }
  if (a.biteSec !== b.biteSec) patch.bite_window_ms = Math.round(a.biteSec * 1000)
  if (a.budget !== b.budget) patch.llm_budget_per_day = a.budget
  if (a.animateEmpty !== b.animateEmpty) patch.animate_empty = a.animateEmpty
  if (a.hudAnimation !== b.hudAnimation) patch.hud_animation = a.hudAnimation
  if (fishing.busy) return
  try {
    await saveFishingSettings(patch)
    syncDraft()
    message.success('已保存')
  } catch (e) {
    message.error(errorText(e), { duration: 6000 })
  }
}
const num = (v: number | null, fallback: number): number => v ?? fallback

const sim = ref<FishingSimulation | null>(null)
const simBusy = ref(false)
async function simulate(): Promise<void> {
  if (fishing.busy) return
  simBusy.value = true
  try {
    sim.value = await simulateFishing()
  } catch (e) {
    message.error(errorText(e))
  } finally {
    simBusy.value = false
  }
}
const OUTCOME_SIM: Record<string, string> = { empty: '空军', fish: '钓到鱼', junk: '杂物', bottle: '漂流瓶' }
const simShares = computed(() => {
  const r = sim.value
  if (!r) return []
  const total = Object.values(r.outcomes).reduce((x, y) => x + y, 0) || 1
  return Object.entries(r.outcomes).map(([k, v]) => ({ key: k, label: OUTCOME_SIM[k] ?? k, pct: Math.round((v / total) * 1000) / 10 }))
})
const simRarities = computed(() => (sim.value ? (Object.keys(RARITY_LABEL) as FishRarity[]).map((k) => ({ key: k, label: RARITY_LABEL[k], n: sim.value?.rarities[k] ?? 0 })) : []))
const simSpecies = computed(() => (sim.value ? Object.entries(sim.value.species).slice(0, 8) : []))

const tabBag = computed(() => `背包（${bagTotal.value}）`)
const tabCodex = computed(() => `图鉴（${s.value?.found ?? 0}/${speciesList.value.length}）`)
const tabBottle = computed(() => `漂流瓶（${s.value?.bottles.length ?? 0}）`)
const codexPercent = computed(() => (speciesList.value.length ? Math.round(((s.value?.found ?? 0) / speciesList.value.length) * 100) : 0))
</script>

<template>
  <backend-offline v-if="fishing.offline || !fishing.loaded" :error="fishing.error" :loading="fishing.loading" @retry="loadFishing" />
  <div v-else-if="s" class="fishing">
    <n-alert v-if="s.error" type="warning" :show-icon="false" class="top-alert">{{ s.error }}</n-alert>

    <n-tabs type="line" animated>
      <!-- 钓场 -->
      <n-tab-pane name="spot" tab="钓场">
        <div class="status card">
          <n-switch :value="s.enabled" :disabled="fishing.busy" @update:value="onToggle" />
          <span class="status-text">
            在{{ placeName }} · {{ s.time_slot }} · 天气{{ s.weather }} · 竿{{ s.rod_name }} · 金币 <b class="num">{{ s.coins }}</b>
          </span>
          <span class="countdown num">{{ countdownText }}</span>
        </div>
        <p class="hint">
          每一竿抛出去后要等 {{ waitText(s.settings.wait_min_sec) }} 到 {{ waitText(s.settings.wait_max_sec) }} 不等才有动静（真实时间，事先看不出来）。
          稀有鱼咬钩时桌宠旁的小窗会出现“!”，几秒内点一下必中；没点的话{{ petName }}有 {{ hookPercent }}% 的概率自己抓住。
          <template v-if="s.empty_streak >= 1">当前连续空军 {{ s.empty_streak }} 次。</template>
        </p>

        <h3 class="sec">地点</h3>
        <div class="grid-cards">
          <div v-for="l in s.locations" :key="l.id" class="card place" :class="{ current: l.id === s.location }">
            <span class="card-title">{{ l.name }}</span>
            <n-tag v-if="l.id === s.location" size="small" round :bordered="false">当前</n-tag>
            <template v-else-if="l.unlocked">
              <n-button size="small" :disabled="fishing.busy" @click="run('place', [l.id])">前往</n-button>
            </template>
            <template v-else>
              <n-button size="small" :disabled="fishing.busy || l.requires_rod_level > s.rod_level" @click="run('unlock', [l.id])">解锁 {{ l.unlock_cost }} 金币</n-button>
              <span v-if="l.requires_rod_level > s.rod_level" class="hint small">需要{{ l.requires_rod }}</span>
            </template>
          </div>
        </div>

        <h3 class="sec">鱼饵</h3>
        <div class="rows">
          <div v-for="b in s.baits" :key="b.id" class="card row" :class="{ current: b.id === s.bait }">
            <span class="card-title">{{ b.name }}</span>
            <span class="num dim">单价 {{ b.cost }}</span>
            <span class="num dim">库存 {{ b.qty === null ? '∞' : b.qty }}</span>
            <n-button size="small" :disabled="fishing.busy || b.id === s.bait" @click="run('bait', [b.id])">{{ b.id === s.bait ? '使用中' : '使用' }}</n-button>
            <template v-if="b.id !== 'none'">
              <n-input-number :value="qtyOf(b.id)" :min="1" :max="99" size="small" class="qty" @update:value="(v: number | null) => setQty(b.id, v)" />
              <n-button size="small" :disabled="fishing.busy" @click="run('buy', [b.id, String(qtyOf(b.id))])">购买</n-button>
            </template>
          </div>
        </div>

        <h3 class="sec">鱼竿</h3>
        <div class="card row">
          <span class="card-title">{{ s.rod_name }}</span>
          <n-button v-if="s.next_rod" size="small" :disabled="fishing.busy" @click="run('rod')">升级到{{ s.next_rod.name }} {{ s.next_rod.cost }} 金币</n-button>
          <span v-else class="dim">已是最高级</span>
        </div>
      </n-tab-pane>

      <!-- 背包 -->
      <n-tab-pane name="bag" :tab="tabBag">
        <n-empty v-if="bagTotal === 0" description="背包里还没有鱼" class="empty" />
        <template v-else>
          <n-space align="center" class="toolbar">
            <n-button size="small" :disabled="fishing.busy" @click="run('sell', ['全部普通'])">卖出全部普通和少见</n-button>
            <n-button size="small" :disabled="fishing.busy" @click="run('sell', ['全部'])">全部卖出</n-button>
            <n-radio-group v-model:value="bagRarity" size="small">
              <n-radio-button v-for="o in bagRarityOptions" :key="o.value" :value="o.value" :label="o.label" />
            </n-radio-group>
            <n-select v-model:value="bagSort" :options="bagSortOptions" size="small" class="sel" />
          </n-space>
          <div class="grid-cards bag">
            <div v-for="f in bag" :key="f.id" class="card fish">
              <div class="fish-img" v-html="bagIcon(f)"></div>
              <span class="card-title">{{ f.name }}</span>
              <span class="num dim">{{ sizeText(f.size_cm) }}</span>
              <n-space :size="4" justify="center">
                <n-tag size="small" round :bordered="false" :type="RARITY_TAG[f.rarity]">{{ f.rarity_label }}</n-tag>
                <n-tag v-if="f.trophy" size="small" round :bordered="false">巨物</n-tag>
              </n-space>
              <span class="num">{{ f.price }} 金币</span>
              <n-space :size="6">
                <n-button size="tiny" :disabled="fishing.busy" @click="run('sell', ['#' + f.id])">卖出</n-button>
                <n-button size="tiny" quaternary :disabled="fishing.busy" @click="run('release', ['#' + f.id])">放生</n-button>
              </n-space>
            </div>
          </div>
        </template>
      </n-tab-pane>

      <!-- 图鉴 -->
      <n-tab-pane name="codex" :tab="tabCodex">
        <svg width="0" height="0" style="position: absolute" aria-hidden="true">
          <filter id="ink-stamp" x="-4%" y="-6%" width="108%" height="112%" color-interpolation-filters="sRGB">
            <feTurbulence type="fractalNoise" baseFrequency="0.045" numOctaves="2" seed="3" result="warp" />
            <feDisplacementMap in="SourceGraphic" in2="warp" scale="2.6" xChannelSelector="R" yChannelSelector="G" result="wobbly" />
            <feTurbulence type="fractalNoise" baseFrequency="0.85" numOctaves="2" seed="11" result="grain" />
            <feColorMatrix in="grain" type="matrix" values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  5 0 0 0 -0.85" result="mask" />
            <feComposite in="wobbly" in2="mask" operator="in" />
          </filter>
        </svg>
        <n-progress type="line" :percentage="codexPercent" :show-indicator="false" class="progress" />
        <n-space align="center" class="toolbar">
          <span class="num dim">已发现 {{ s.found }} / {{ speciesList.length }}</span>
          <n-select v-model:value="codexPlace" :options="placeOptions" size="small" class="sel" />
          <n-select v-model:value="codexSort" :options="codexSortOptions" size="small" class="sel" />
          <n-space align="center" :size="6">
            <n-switch v-model:value="inkOn" size="small" />
            <span>墨迹效果</span>
          </n-space>
        </n-space>
        <div class="codex-wrap">
          <div class="codex">
            <button v-for="sp in codexList" :key="sp.id" type="button" class="paper" :class="{ locked: !sp.found }" :disabled="!sp.found" @click="openDetail(sp)">
              <div class="paper-img" v-html="codexSvg[sp.id]"></div>
              <template v-if="sp.found">
                <span class="paper-name">{{ sp.name }}</span>
                <span class="paper-sub">{{ sp.rarity_label }}</span>
                <span class="paper-sub num">最大 {{ sizeText(sp.max_size ?? 0) }} · {{ sp.count ?? 0 }} 条</span>
                <span v-if="sp.trophy" class="seal" aria-hidden="true">拓</span>
              </template>
              <template v-else>
                <span class="paper-name">？？？</span>
                <span class="paper-sub">{{ sp.locations.join('、') }}</span>
              </template>
            </button>
          </div>
        </div>
        <div v-if="junkList.length" class="junk">
          <span class="dim">杂物统计</span>
          <span v-for="j in junkList" :key="j.id" class="junk-item">
            <span class="junk-icon" v-html="j.svg"></span>{{ j.name }} ×{{ j.count }}
          </span>
        </div>
      </n-tab-pane>

      <!-- 漂流瓶 -->
      <n-tab-pane name="bottles" :tab="tabBottle">
        <n-empty v-if="!s.bottles.length" description="还没捞到过漂流瓶" class="empty" />
        <div v-else class="letters">
          <div v-for="(b, i) in s.bottles" :key="i" class="letter">
            <p class="letter-body">{{ b.body }}</p>
            <p class="letter-sign">—— {{ b.sender }}</p>
            <p class="letter-date num">{{ fmtDate(b.read_ts) }}</p>
          </div>
        </div>
      </n-tab-pane>

      <!-- 记录与设置 -->
      <n-tab-pane name="log" tab="记录与设置">
        <p class="totals num">累计：{{ s.totals.casts }} 竿 / {{ s.totals.fish }} 条 / 跑了 {{ s.totals.escaped }} 条</p>

        <h3 class="sec">最近记录</h3>
        <n-empty v-if="!logRows.length" description="还没有记录" class="empty" />
        <table v-else class="log">
          <thead>
            <tr><th>时间</th><th>结果</th><th>名称</th><th>地点 · 时段 · 天气</th></tr>
          </thead>
          <tbody>
            <tr v-for="e in logRows" :key="e.seq">
              <td class="num">{{ fmtHm(e.ts) }}</td>
              <td>{{ OUTCOME_TEXT[e.outcome] }}</td>
              <td>{{ logName(e) }}</td>
              <td>{{ e.location }} · {{ e.time_slot }} · {{ e.weather }}</td>
            </tr>
          </tbody>
        </table>

        <h3 class="sec">设置</h3>
        <div class="form card">
          <label class="f-row">
            <span class="f-label">每竿最短等待（分钟）</span>
            <n-input-number :value="draft.waitMin" :min="3" :max="360" :precision="0" size="small" @update:value="(v: number | null) => (draft.waitMin = num(v, draft.waitMin))" />
          </label>
          <label class="f-row">
            <span class="f-label">每竿最长等待（分钟）</span>
            <n-input-number :value="draft.waitMax" :min="3" :max="720" :precision="0" size="small" @update:value="(v: number | null) => (draft.waitMax = num(v, draft.waitMax))" />
            <span class="dim">每一竿在两者之间随机，短的多、长的少；对下一竿生效</span>
          </label>
          <label class="f-row">
            <span class="f-label">咬钩窗口（秒）</span>
            <n-input-number :value="draft.biteSec" :min="1" :max="10" :step="0.5" size="small" @update:value="(v: number | null) => (draft.biteSec = num(v, draft.biteSec))" />
          </label>
          <label class="f-row">
            <span class="f-label">每日 LLM 调用上限</span>
            <n-input-number :value="draft.budget" :min="0" :max="500" size="small" @update:value="(v: number | null) => (draft.budget = num(v, draft.budget))" />
            <span class="dim">今天已用 {{ s.llm_used_today }} 次</span>
          </label>
          <p v-if="!s.llm_available" class="hint small">还没有指定聊天用的 LLM，钓鱼只用内置台词</p>
          <div class="f-row">
            <span class="f-label">空军也播放完整动画</span>
            <n-switch :value="draft.animateEmpty" @update:value="(v: boolean) => (draft.animateEmpty = v)" />
          </div>
          <div class="f-row">
            <span class="f-label">小窗动画</span>
            <n-switch :value="draft.hudAnimation" @update:value="(v: boolean) => (draft.hudAnimation = v)" />
            <span class="dim">关闭 = 轻量模式：只换静态画面，适合性能一般的电脑</span>
          </div>
          <div class="f-row">
            <n-button type="primary" size="small" :disabled="!dirty || fishing.busy" @click="saveSettings">保存设置</n-button>
          </div>
        </div>

        <h3 class="sec">概率模拟</h3>
        <n-space align="center">
          <n-button size="small" :loading="simBusy" :disabled="fishing.busy" @click="simulate">概率模拟</n-button>
          <span class="dim">用当前地点、鱼饵、鱼竿模拟 1 万竿，不影响存档</span>
        </n-space>
        <div v-if="sim" class="card sim">
          <p>各结果占比：<span v-for="r in simShares" :key="r.key" class="chip num">{{ r.label }} {{ r.pct }}%</span></p>
          <p>各稀有度条数：<span v-for="r in simRarities" :key="r.key" class="chip num">{{ r.label }} {{ r.n }}</span></p>
          <p class="num">每天金币约 {{ sim.coins_per_day }}</p>
          <p class="num">
            <template v-if="sim.days_per_legendary === null">传说鱼：这个地点/时段条件下钓不到</template>
            <template v-else>传说鱼平均 {{ sim.days_per_legendary }} 天一条</template>
          </p>
          <p>
            前 8 个物种：
            <span v-for="[name, n] in simSpecies" :key="name" class="chip num">{{ name }} {{ n }}</span>
          </p>
        </div>
      </n-tab-pane>
    </n-tabs>

    <!-- species detail -->
    <n-modal v-model:show="detailOpen" preset="card" :title="detail?.name ?? ''" class="detail-modal" :style="{ width: 'min(520px, 92vw)' }">
      <template v-if="detail">
        <div class="paper detail-paper">
          <div class="paper-img big" v-html="codexSvg[detail.id]"></div>
        </div>
        <n-space align="center" class="d-head">
          <b>{{ detail.name }}</b>
          <n-tag size="small" round :bordered="false" :type="RARITY_TAG[detail.rarity ?? 'common']">{{ detail.rarity_label }}</n-tag>
        </n-space>
        <ul class="d-list num">
          <li>尺寸：{{ sizeText(detail.min_size ?? 0) }} / {{ sizeText(detail.avg_size ?? 0) }} / {{ sizeText(detail.max_size ?? 0) }}（最小 / 平均 / 最大）</li>
          <li>累计数量：{{ detail.count ?? 0 }}　放生数量：{{ detail.released ?? 0 }}</li>
          <li>首次钓到：{{ fmtDateTime(detail.first_ts) }}</li>
          <li>出没地点：{{ detail.locations.join('、') }}</li>
          <li>出没时间：{{ (detail.time ?? []).join('、') }}</li>
          <li>天气：{{ (detail.weather ?? []).join('、') }}</li>
          <li>偏好饵：{{ detail.baits && detail.baits.length ? detail.baits.join('、') : '无' }}</li>
        </ul>
        <p class="d-story">{{ detail.story || `关于${detail.name}的笔记还没写完。` }}</p>
      </template>
    </n-modal>

    <!-- confirm dialog -->
    <n-modal :show="confirmAsking" preset="dialog" type="warning" title="确认" :content="confirmText" positive-text="确定" negative-text="取消" :mask-closable="false" @positive-click="answerConfirm(true)" @negative-click="answerConfirm(false)" @esc="answerConfirm(false)" />
  </div>
</template>

<style scoped>
.num {
  font-variant-numeric: tabular-nums;
}
.dim {
  opacity: 0.65;
}
.hint {
  font-size: 0.9em;
  line-height: 1.7;
  opacity: 0.65;
  margin: 8px 0;
}
.hint.small {
  font-size: 0.82em;
  margin: 0;
}
.top-alert {
  margin-bottom: 12px;
}
.sec {
  margin: 20px 0 10px;
  font-size: 1.05em;
  font-weight: 600;
}
.empty {
  margin: 40px 0;
}
.toolbar {
  margin-bottom: 14px;
}
.sel {
  width: 150px;
}
.card {
  border: 1.5px solid currentColor;
  border-radius: 12px;
  background: rgba(128, 128, 128, 0.06);
  padding: 10px 14px;
  transition: transform 0.15s, background 0.15s;
}
.card:hover {
  transform: translateY(-2px);
  background: rgba(128, 128, 128, 0.12);
}
.card.current {
  border-width: 2.5px;
  background: rgba(128, 128, 128, 0.14);
}
.card-title {
  font-weight: 600;
}
.status {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
}
.status-text {
  flex: 1 1 260px;
}
.countdown {
  font-weight: 600;
}
.grid-cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 12px;
}
.grid-cards.bag {
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
}
.place {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
}
.rows {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
}
.row .card-title {
  min-width: 80px;
}
.qty {
  width: 90px;
}
.fish {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  text-align: center;
}
.fish-img {
  width: 72px;
  height: 72px;
}
.fish-img :deep(svg),
.junk-icon :deep(svg),
.paper-img :deep(svg) {
  width: 100%;
  height: 100%;
  display: block;
}
.progress {
  margin-bottom: 10px;
}
.codex-wrap {
  container-type: inline-size;
}
.codex {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}
@container (max-width: 560px) {
  .codex {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
.paper {
  all: unset;
  box-sizing: border-box;
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  padding: 12px 10px 14px;
  text-align: center;
  background: #f4efe4;
  color: #14110f;
  border: 1.5px solid #14110f;
  border-radius: 12px;
  cursor: pointer;
  transition: transform 0.15s;
}
.paper:hover:not(:disabled),
.paper:focus-visible {
  transform: translateY(-3px);
}
.paper.locked {
  cursor: default;
}
.paper-img {
  width: 100%;
  aspect-ratio: 5 / 3;
}
.paper-img.big {
  aspect-ratio: 5 / 3;
  max-width: 360px;
  margin: 0 auto;
}
.paper-name {
  font-weight: 700;
}
.paper-sub {
  font-size: 0.82em;
  opacity: 0.7;
}
.seal {
  position: absolute;
  right: 8px;
  bottom: 8px;
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #b3261e;
  color: #f4efe4;
  border-radius: 3px;
  font-size: 14px;
  font-weight: 700;
  line-height: 1;
  transform: rotate(-6deg);
  opacity: 0.9;
}
.detail-paper {
  cursor: default;
  padding: 16px;
  margin-bottom: 12px;
}
.d-head {
  margin-bottom: 8px;
}
.d-list {
  margin: 0 0 12px;
  padding-left: 18px;
  line-height: 1.9;
}
.d-story {
  margin: 0;
  line-height: 1.8;
  opacity: 0.85;
}
.junk {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 14px;
  margin-top: 18px;
}
.junk-item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-variant-numeric: tabular-nums;
}
.junk-icon {
  width: 24px;
  height: 24px;
  display: inline-block;
}
.letters {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 14px;
}
.letter {
  background: #f4efe4;
  color: #14110f;
  border: 1.5px solid #14110f;
  border-radius: 10px;
  padding: 14px 16px;
  transition: transform 0.15s;
}
.letter:hover {
  transform: translateY(-2px);
}
.letter-body {
  margin: 0 0 10px;
  line-height: 1.8;
  white-space: pre-wrap;
}
.letter-sign {
  margin: 0;
  text-align: right;
  font-weight: 600;
}
.letter-date {
  margin: 4px 0 0;
  text-align: right;
  font-size: 0.8em;
  opacity: 0.6;
}
.totals {
  margin: 4px 0;
}
.log {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.92em;
}
.log th,
.log td {
  text-align: left;
  padding: 6px 10px;
  border-bottom: 1px solid rgba(128, 128, 128, 0.3);
}
.form {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.form:hover {
  transform: none;
}
.f-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
}
.f-label {
  min-width: 150px;
}
.sim {
  margin-top: 12px;
  line-height: 1.9;
}
.sim:hover {
  transform: none;
}
.sim p {
  margin: 0;
}
.chip {
  display: inline-block;
  margin-right: 12px;
}
@media (prefers-reduced-motion: reduce) {
  .card,
  .paper,
  .letter {
    transition: none;
  }
  .card:hover,
  .paper:hover:not(:disabled),
  .letter:hover {
    transform: none;
  }
}
</style>
