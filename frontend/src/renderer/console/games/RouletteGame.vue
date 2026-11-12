<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import {
  NButton,
  NCollapse,
  NCollapseItem,
  NInputNumber,
  NPopconfirm,
  NPopover,
  NRadio,
  NRadioGroup,
  NSpace,
  NSpin,
  NSwitch,
  NTag,
  NTooltip
} from 'naive-ui'
import type { GameSide, RouletteItem, RouletteLog } from '../../../shared/backend'
import { ui } from '../store'
import { games, ITEM_INFO, logText, petName, useGameRun, who } from '../games'
import { GameIcon } from './GameIcon'
import PixelWheel from './PixelWheel.vue'
import GlassModal from './GlassModal.vue'
import GameOver from './GameOver.vue'

const emit = defineEmits<{ back: [] }>()
const run = useGameRun()
const accent = computed(() => ui.value.accent)

const r = computed(() => games.state?.roulette ?? null)
const ITEMS: RouletteItem[] = ['juice', 'dice', 'flash', 'lens', 'driver']

// --- timers ---
const timers = new Set<number>()
function later(fn: () => void, ms: number): number {
  const id = window.setTimeout(() => {
    timers.delete(id)
    fn()
  }, ms)
  timers.add(id)
  return id
}
function cancel(id: number): void {
  clearTimeout(id)
  timers.delete(id)
}
onBeforeUnmount(() => {
  timers.forEach((id) => clearTimeout(id))
  timers.clear()
})

// --- settings ---
const mode = ref<'classic' | 'custom'>('classic')
const bullets = ref(6)
const playerHp = ref(4)
const petHp = ref(4)
const useLlm = ref(true)
watch(
  () => games.state?.llm_available,
  (ok) => {
    if (ok === false) useLlm.value = false
  },
  { immediate: true }
)
const startBody = computed(() =>
  mode.value === 'classic'
    ? { mode: 'classic', bullets: 6, player_hp: 4, pet_hp: 4, use_llm: useLlm.value }
    : {
        mode: 'custom',
        bullets: Math.min(12, Math.max(2, bullets.value || 6)),
        player_hp: Math.min(9, Math.max(1, playerHp.value || 4)),
        pet_hp: Math.min(9, Math.max(1, petHp.value || 4)),
        use_llm: useLlm.value
      }
)

// --- stage ---
const stage = ref<'setup' | 'wheel' | 'play'>(r.value ? 'play' : 'setup')
const wheelFirst = ref<GameSide>('player')
const wheelTitle = ref('')
const wheelKey = ref(0)
const modalRound = ref<number | null>(null)
const pendingModal = ref(false)
const showOver = ref(false)
const petFailed = ref(false)
const diceOpen = ref(false)

let seenSeq = r.value ? Math.max(-1, ...r.value.log.map((e) => e.seq)) : -1
let prevLen = r.value ? r.value.log.length : 0
if (r.value && r.value.winner !== null) showOver.value = true

function enterWheel(): void {
  if (!r.value) return
  wheelFirst.value = r.value.first
  wheelTitle.value = `第 ${r.value.round} 轮`
  wheelKey.value++
  stage.value = 'wheel'
}

// --- effects ---
interface Fx {
  kind: 'hit' | 'miss' | 'item'
  /** the side whose panel it shows on */
  side: GameSide
  text: string
  item?: RouletteItem
  key: number
}
const fx = ref<Fx | null>(null)
let fxTimer = 0
let fxKey = 0
function playFx(e: RouletteLog): void {
  cancel(fxTimer)
  const other: GameSide = e.side === 'player' ? 'pet' : 'player'
  if (e.kind === 'shoot') {
    if (e.live) {
      const hit = e.target === 'self' ? e.side : other
      fx.value = { kind: 'hit', side: hit, text: `砰！-${e.damage ?? 1}`, key: ++fxKey }
    } else {
      fx.value = { kind: 'miss', side: e.side, text: '咔哒…假弹', key: ++fxKey }
    }
  } else if (e.kind === 'item' && e.item) {
    const extra = e.item === 'dice' && e.roll !== undefined ? ` ${e.roll}` : ''
    fx.value = { kind: 'item', side: e.side, text: ITEM_INFO[e.item].name + extra, item: e.item, key: ++fxKey }
  } else return
  fxTimer = later(() => (fx.value = null), 900)
}

watch(r, (cur) => {
  if (!cur) {
    seenSeq = -1
    prevLen = 0
    stage.value = 'setup'
    showOver.value = false
    petFailed.value = false
    return
  }
  if (cur.log.length < prevLen) {
    seenSeq = -1
    showOver.value = false
    petFailed.value = false
  }
  prevLen = cur.log.length
  const fresh = cur.log.filter((e) => e.seq > seenSeq)
  if (!fresh.length) return
  seenSeq = Math.max(seenSeq, ...fresh.map((e) => e.seq))
  const act = [...fresh].reverse().find((e) => e.kind === 'shoot' || e.kind === 'item')
  if (act) playFx(act)
  const delay = act ? 1000 : 0
  const next = fresh.find((e) => e.kind === 'load' && (e.round ?? 0) >= 2)
  if (next) {
    pendingModal.value = true
    later(() => {
      pendingModal.value = false
      modalRound.value = next.round ?? cur.round
    }, delay)
  }
  if (cur.winner !== null) later(() => (showOver.value = true), delay)
})

function onModalClose(): void {
  if (modalRound.value === null) return
  modalRound.value = null
  enterWheel()
}

// --- derived ---
const overlay = computed(() => stage.value !== 'play' || modalRound.value !== null || pendingModal.value || showOver.value)
const canAct = computed(() => !!r.value && r.value.turn === 'player' && r.value.winner === null && !games.busy && !overlay.value)
function itemDisabled(item: RouletteItem): boolean {
  const g = r.value
  if (!g || !canAct.value) return true
  if (item === 'juice' && g.hp.player >= g.max_hp.player) return true
  if (item === 'flash' && g.boost) return true
  return false
}
const lastSay = computed(() => {
  const log = r.value?.log ?? []
  for (let i = log.length - 1; i >= 0; i--) if (log[i].say) return log[i].say as string
  return ''
})
const logList = computed(() => [...(r.value?.log ?? [])].reverse().filter((e) => logText(e)))
const petThinking = computed(
  () => !!r.value && r.value.turn === 'pet' && r.value.winner === null && stage.value === 'play' && !overlay.value && !petFailed.value
)

// --- the other side's turn ---
let petTimer = 0
watch(
  [() => !overlay.value && !games.busy && !!r.value && r.value.turn === 'pet' && r.value.winner === null && !petFailed.value, () => r.value?.seq],
  ([ok]) => {
    cancel(petTimer)
    if (!ok) return
    petTimer = later(async () => {
      if (games.busy) return
      const done = await run('/api/games/roulette/pet')
      if (!done) petFailed.value = true
    }, 1200)
  },
  { immediate: true }
)
function retryPet(): void {
  petFailed.value = false
}

// --- actions ---
async function onStart(): Promise<void> {
  petFailed.value = false
  showOver.value = false
  if (await run('/api/games/roulette/start', startBody.value)) enterWheel()
}
async function shoot(target: 'self' | 'opponent'): Promise<void> {
  await run('/api/games/roulette/move', { action: 'shoot', target })
}
async function useItem(item: RouletteItem, guess?: 'big' | 'small'): Promise<void> {
  diceOpen.value = false
  await run('/api/games/roulette/move', guess ? { action: 'item', item, guess } : { action: 'item', item })
}
async function quit(): Promise<void> {
  if (await run('/api/games/roulette/quit')) stage.value = 'setup'
}
async function again(): Promise<void> {
  await onStart()
}
async function back(): Promise<void> {
  if (await run('/api/games/roulette/quit')) emit('back')
}
</script>

<template>
  <div class="rl">
    <!-- setup -->
    <div v-if="stage === 'setup'" class="rl-setup">
      <n-collapse>
        <n-collapse-item title="规则说明" name="rules">
          <ul class="rl-rules">
            <li>每一轮真弹、假弹的数量是公开的，但子弹的顺序没人知道。</li>
            <li>朝自己开枪：打到假弹可以继续行动，打到真弹扣血并换人。</li>
            <li>朝对方开枪：无论真假，都换人。</li>
            <li>子弹打空算一轮结束：重新转轮盘决定先手，双方道具清空并各发 3 个新的，生命保留。</li>
            <li>道具：</li>
          </ul>
          <div v-for="it in ITEMS" :key="it" class="rl-rule-item">
            <game-icon :name="it" :size="22" />
            <b>{{ ITEM_INFO[it].name }}</b>
            <span>{{ ITEM_INFO[it].desc }}</span>
          </div>
          <div class="rl-rules" style="margin-top: 8px">生命先归零的一方输。</div>
        </n-collapse-item>
      </n-collapse>

      <div class="rl-row">
        <n-radio-group v-model:value="mode">
          <n-space>
            <n-radio value="classic">经典局（6 发，双方 4 点生命）</n-radio>
            <n-radio value="custom">自定义局</n-radio>
          </n-space>
        </n-radio-group>
      </div>
      <div v-if="mode === 'custom'" class="rl-row rl-custom">
        <label>子弹数量 <n-input-number v-model:value="bullets" :min="2" :max="12" style="width: 120px" /></label>
        <label>你的生命 <n-input-number v-model:value="playerHp" :min="1" :max="9" style="width: 120px" /></label>
        <label>{{ petName }}的生命 <n-input-number v-model:value="petHp" :min="1" :max="9" style="width: 120px" /></label>
      </div>
      <div class="rl-row">
        <n-switch v-model:value="useLlm" :disabled="games.state?.llm_available === false" />
        <span style="margin-left: 8px">让{{ petName }}自己思考并说话（每一步调用一次 LLM）</span>
      </div>
      <div class="rl-row">
        <n-button type="primary" :loading="games.busy" :disabled="games.busy" @click="onStart">开始</n-button>
      </div>
    </div>

    <!-- wheel -->
    <pixel-wheel v-else-if="stage === 'wheel'" :key="wheelKey" :first="wheelFirst" :title="wheelTitle" @done="stage = 'play'" />

    <!-- play -->
    <div v-else-if="r" class="rl-play">
      <div class="rl-sides">
        <div v-for="side in (['player', 'pet'] as GameSide[])" :key="side" class="rl-panel" :class="{ active: r.turn === side && r.winner === null, shake: fx && fx.kind === 'hit' && fx.side === side, [`hit-${fx?.key}`]: false }" :data-fxkey="fx?.key">
          <div class="rl-name">
            <b>{{ who(side) }}</b>
            <n-tag v-if="r.turn === side && r.winner === null" size="small" type="primary">行动中</n-tag>
          </div>
          <div class="rl-hp">
            <game-icon v-for="i in r.hp[side]" :key="'f' + i" name="heart" :size="22" />
            <game-icon v-for="i in Math.max(0, r.max_hp[side] - r.hp[side])" :key="'e' + i" name="heart-empty" :size="22" />
            <span class="rl-num">{{ r.hp[side] }}/{{ r.max_hp[side] }}</span>
          </div>
          <div v-if="side === 'pet'" class="rl-petitems">
            <span>道具 ×{{ r.pet_items }}</span>
            <i v-for="i in r.pet_items" :key="i" class="rl-back" />
          </div>
          <div v-if="side === 'pet' && lastSay" class="rl-bubble">{{ lastSay }}</div>
          <div v-if="fx && fx.side === side" :key="fx.key" class="rl-fx" :class="fx.kind">
            <game-icon v-if="fx.item" :name="fx.item" :size="26" />
            <span>{{ fx.text }}</span>
          </div>
        </div>
      </div>

      <div class="rl-chamber">
        <div>本轮剩 {{ r.left }} 发：真弹 {{ r.live }}，假弹 {{ r.blank }}</div>
        <div class="rl-bullets">
          <span v-for="i in r.left" :key="i" class="rl-bullet" :class="{ cur: i === 1 }">
            <game-icon name="bullet" :size="22" />
            <b v-if="i === 1 && r.peek !== null" class="rl-peek">{{ r.peek === 1 ? '真' : '假' }}</b>
          </span>
        </div>
        <div v-if="r.peek !== null" class="rl-hint">第一发是{{ r.peek === 1 ? '真弹' : '假弹' }}（只有你知道）</div>
        <div v-if="r.boost" class="rl-hint">{{ who(r.turn) }}的震爆弹已生效：下一枪真弹伤害 2</div>
      </div>

      <div class="rl-items">
        <span class="rl-label">你的道具</span>
        <template v-for="(it, idx) in r.items" :key="idx + it">
          <n-popover v-if="it === 'dice'" v-model:show="diceOpen" trigger="click" :disabled="itemDisabled(it)">
            <template #trigger>
              <n-button :disabled="itemDisabled(it)">
                <template #icon><game-icon :name="it" :size="20" /></template>
                {{ ITEM_INFO[it].name }}
              </n-button>
            </template>
            <div class="rl-guess">
              <div>{{ ITEM_INFO[it].desc }}</div>
              <n-space style="margin-top: 8px">
                <n-button size="small" :disabled="games.busy" @click="useItem('dice', 'big')">猜大</n-button>
                <n-button size="small" :disabled="games.busy" @click="useItem('dice', 'small')">猜小</n-button>
              </n-space>
            </div>
          </n-popover>
          <n-tooltip v-else>
            <template #trigger>
              <n-button :disabled="itemDisabled(it)" @click="useItem(it)">
                <template #icon><game-icon :name="it" :size="20" /></template>
                {{ ITEM_INFO[it].name }}
              </n-button>
            </template>
            {{ ITEM_INFO[it].desc }}
          </n-tooltip>
        </template>
        <span v-if="!r.items.length" class="rl-dim">没有道具了</span>
      </div>

      <div class="rl-shoot">
        <n-button size="large" type="primary" :disabled="!canAct" @click="shoot('opponent')">朝对方开枪</n-button>
        <n-button size="large" :disabled="!canAct" @click="shoot('self')">朝自己开枪</n-button>
      </div>

      <div v-if="petThinking" class="rl-think"><n-spin :size="16" /> <span>{{ petName }}正在想…</span></div>
      <div v-if="petFailed && r.turn === 'pet' && r.winner === null" class="rl-think">
        <span>{{ petName }}这一步没走成。</span>
        <n-button size="small" :disabled="games.busy" @click="retryPet">重试</n-button>
      </div>

      <div class="rl-log">
        <div v-for="e in logList" :key="e.seq" class="rl-logline">
          {{ logText(e) }}<template v-if="e.say"> —— “{{ e.say }}”</template>
        </div>
      </div>

      <div class="rl-foot">
        <n-popconfirm @positive-click="quit">
          <template #trigger><n-button size="small" quaternary :disabled="games.busy">放弃这一局</n-button></template>
          放弃后这一局不计战绩，确定吗？
        </n-popconfirm>
      </div>
    </div>

    <glass-modal
      :show="modalRound !== null"
      :title="`第 ${modalRound ?? ''} 轮`"
      subtitle="子弹打空了。上一轮的道具已清空并重新发放，生命值保留；重新转轮盘决定先手。"
      :auto-close="2600"
      @close="onModalClose"
    />

    <game-over
      v-if="r"
      :show="showOver && r.winner !== null"
      :outcome="r.winner === 'player' ? 'win' : 'lose'"
      :title="r.winner === 'player' ? '你赢了' : `${petName}赢了`"
      :end="r.end"
      @again="again"
      @back="back"
    >
      <div>共 {{ r.round }} 轮 · 你 {{ r.hp.player }}/{{ r.max_hp.player }} · {{ petName }} {{ r.hp.pet }}/{{ r.max_hp.pet }}</div>
    </game-over>
  </div>
</template>

<style scoped>
.rl {
  position: relative;
}
.rl-row {
  margin-top: 14px;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
}
.rl-custom label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.rl-rules {
  margin: 0;
  padding-left: 18px;
  line-height: 1.8;
}
.rl-rule-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 3px 0 3px 18px;
}
.rl-rule-item span {
  opacity: 0.75;
}
.rl-sides {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
}
.rl-panel {
  position: relative;
  padding: 12px 14px;
  min-height: 112px;
  border: 2px solid color-mix(in srgb, currentColor 18%, transparent);
  border-radius: 12px;
  background: color-mix(in srgb, currentColor 4%, transparent);
  transition: border-color 0.2s;
}
.rl-panel.active {
  border-color: v-bind(accent);
}
.rl-panel.shake {
  animation: rl-shake 0.45s, rl-flash 0.9s;
}
.rl-name {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 1.05em;
}
.rl-hp {
  margin-top: 8px;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 2px;
}
.rl-num {
  margin-left: 8px;
  font-variant-numeric: tabular-nums;
}
.rl-petitems {
  margin-top: 8px;
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 0.9em;
}
.rl-back {
  width: 12px;
  height: 16px;
  border-radius: 2px;
  border: 1px solid currentColor;
  background: color-mix(in srgb, currentColor 25%, transparent);
}
.rl-bubble {
  margin-top: 10px;
  padding: 6px 10px;
  border-radius: 10px;
  background: color-mix(in srgb, v-bind(accent) 18%, transparent);
  line-height: 1.5;
}
.rl-fx {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  font-size: 1.6em;
  font-weight: 800;
  pointer-events: none;
  border-radius: 10px;
  background: color-mix(in srgb, var(--rl-fx-bg, transparent) 30%, transparent);
  animation: rl-pop 0.9s ease-out forwards;
}
.rl-fx.hit {
  --rl-fx-bg: #e5484d;
  color: #e5484d;
}
.rl-fx.miss,
.rl-fx.item {
  background: color-mix(in srgb, currentColor 8%, transparent);
}
.rl-chamber {
  margin-top: 16px;
  line-height: 1.8;
}
.rl-bullets {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 4px;
}
.rl-bullet {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 3px 4px;
  border: 1px solid transparent;
  border-radius: 6px;
}
.rl-bullet.cur {
  border-color: v-bind(accent);
  background: color-mix(in srgb, v-bind(accent) 15%, transparent);
}
.rl-peek {
  font-size: 0.9em;
  color: v-bind(accent);
}
.rl-hint {
  font-size: 0.9em;
  color: v-bind(accent);
}
.rl-items {
  margin-top: 14px;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
.rl-label {
  opacity: 0.7;
}
.rl-dim {
  opacity: 0.55;
}
.rl-shoot {
  margin-top: 16px;
  display: flex;
  gap: 14px;
}
.rl-think {
  margin-top: 12px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.rl-log {
  margin-top: 16px;
  max-height: 200px;
  overflow-y: auto;
  padding: 8px 12px;
  border-radius: 8px;
  background: color-mix(in srgb, currentColor 5%, transparent);
  font-size: 0.9em;
  line-height: 1.7;
}
.rl-foot {
  margin-top: 12px;
}
@keyframes rl-shake {
  0%, 100% { transform: translateX(0); }
  20% { transform: translateX(-8px); }
  40% { transform: translateX(8px); }
  60% { transform: translateX(-6px); }
  80% { transform: translateX(6px); }
}
@keyframes rl-flash {
  0%, 40% { box-shadow: 0 0 0 3px #e5484d, 0 0 18px #e5484d; }
  100% { box-shadow: none; }
}
@keyframes rl-pop {
  0% { opacity: 0; transform: scale(0.7); }
  20% { opacity: 1; transform: scale(1.1); }
  80% { opacity: 1; transform: scale(1); }
  100% { opacity: 0; }
}
@media (prefers-reduced-motion: reduce) {
  .rl-panel.shake,
  .rl-fx {
    animation: none;
  }
}
</style>
