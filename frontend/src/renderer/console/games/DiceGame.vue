<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { NAlert, NButton, NCollapse, NCollapseItem, NInputNumber, NPopconfirm, NSpace, NSpin, NSwitch } from 'naive-ui'
import { games, petName, useGameRun, who } from '../games'
import { ui } from '../store'
import { GameIcon } from './GameIcon'
import GameOver from './GameOver.vue'
import GlassModal from './GlassModal.vue'
import PixelWheel from './PixelWheel.vue'

const emit = defineEmits<{ back: [] }>()
const run = useGameRun()
const accent = computed(() => ui.value.accent)

const d = computed(() => games.state?.dice ?? null)
const record = computed(() => games.state?.records.dice ?? { player: 0, pet: 0, draw: 0 })
const useLlm = ref(true)
const llmAvailable = computed(() => games.state?.llm_available ?? false)
watch(
  llmAvailable,
  (v) => {
    if (!v) useLlm.value = false
  },
  { immediate: true }
)

const showWheel = ref(false)
const petFailed = ref(false)

async function start(): Promise<void> {
  petFailed.value = false
  if (await run('/api/games/dice/start', { use_llm: useLlm.value && llmAvailable.value })) showWheel.value = true
}
async function quit(): Promise<void> {
  await run('/api/games/dice/quit')
}
async function backToLobby(): Promise<void> {
  await run('/api/games/dice/quit')
  emit('back')
}

// Bidding form
const bidding = ref(false)
const bidCount = ref<number | null>(1)
const bidFace = ref<number | null>(1)
const lastBid = computed(() => (d.value && d.value.bids.length ? d.value.bids[d.value.bids.length - 1] : null))

function nextLegal(): { count: number; face: number } {
  const l = lastBid.value
  if (!l) return { count: 1, face: 1 }
  return l.face < 6 ? { count: l.count, face: l.face + 1 } : { count: l.count + 1, face: 1 }
}
function openBid(): void {
  const n = nextLegal()
  bidCount.value = Math.min(n.count, d.value?.total ?? n.count)
  bidFace.value = n.face
  bidding.value = true
}
const bidError = computed(() => {
  const l = lastBid.value
  const c = bidCount.value
  const f = bidFace.value
  if (c === null || f === null) return '请填写数量和点数'
  if (!l) return ''
  if (c > l.count || (c === l.count && f > l.face)) return ''
  return `要比 ${l.count} 个 ${l.face} 点更大：数量更多，或数量相同但点数更大`
})
async function submitBid(): Promise<void> {
  if (bidError.value || bidCount.value === null || bidFace.value === null) return
  if (await run('/api/games/dice/move', { action: 'bid', count: bidCount.value, face: bidFace.value })) bidding.value = false
}
async function callIt(): Promise<void> {
  await run('/api/games/dice/move', { action: 'call' })
  bidding.value = false
}
async function nextRound(): Promise<void> {
  bidding.value = false
  await run('/api/games/dice/next')
}

// The other side moves by itself
let petTimer = 0
async function petMove(): Promise<void> {
  const ok = await run('/api/games/dice/pet')
  if (!ok) petFailed.value = true
}
function retryPet(): void {
  petFailed.value = false
}
watch(
  () => [d.value?.phase, d.value?.turn, d.value?.round, d.value?.bids.length, showWheel.value, games.busy, petFailed.value, d.value?.winner],
  () => {
    clearTimeout(petTimer)
    const v = d.value
    if (!v || v.winner !== null || v.phase !== 'bid' || v.turn !== 'pet') return
    if (showWheel.value || games.busy || petFailed.value) return
    petTimer = window.setTimeout(() => void petMove(), 1200)
  },
  { immediate: true }
)
watch(
  () => d.value?.turn,
  () => {
    bidding.value = false
  }
)
onBeforeUnmount(() => clearTimeout(petTimer))

const myTurn = computed(() => !!d.value && d.value.phase === 'bid' && d.value.turn === 'player')
const petTurn = computed(() => !!d.value && d.value.phase === 'bid' && d.value.turn === 'pet')
const showReveal = computed(() => !!d.value && d.value.reveal !== null && d.value.phase === 'reveal')
const showEnd = computed(() => !!d.value && d.value.winner !== null)
const revealSource = computed(() => d.value?.reveal ?? null)
</script>

<template>
  <div class="dice">
    <template v-if="!d">
      <div class="record">战绩：胜 {{ record.player }} · 负 {{ record.pet }} · 平 {{ record.draw }}</div>
      <n-collapse>
        <n-collapse-item title="规则说明" name="rules">
          <div class="rules">
            双方各 5 颗骰子扣在杯下，只能看到自己的。轮流报数「我认为场上一共至少有 n 个 x 点」（场上 = 双方所有骰子，x 为 1~6）。轮到你时加码（数量更多，或数量相同但点数更大）或喊开。喊开后双方亮骰子，x 点总数达到或超过报数则报数方赢、喊开的人掉一颗骰子，不够则报数的人掉一颗。掉骰子的人下一轮先报。先掉光 5 颗的输。
          </div>
        </n-collapse-item>
      </n-collapse>
      <div class="setup">
        <n-switch v-model:value="useLlm" :disabled="!llmAvailable" />
        <span>让{{ petName }}自己思考并说话（每一步调用一次 LLM）</span>
      </div>
      <n-alert v-if="!llmAvailable" type="info" :show-icon="false" style="margin-top: 8px">没有配置聊天用的 LLM，{{ petName }}会按内置策略沉默地玩。</n-alert>
      <n-button type="primary" size="large" style="margin-top: 16px" :loading="games.busy" :disabled="games.busy" @click="start">开始</n-button>
    </template>

    <template v-else>
      <pixel-wheel v-if="showWheel" :first="d.first" title="谁先报数" @done="showWheel = false" />
      <template v-else>
        <div class="sides">
          <div class="side" :class="{ active: d.turn === 'pet' && d.phase === 'bid' && !showEnd }">
            <div class="side-name">{{ petName }}</div>
            <div class="row">
              <game-icon name="cup" :size="40" />
              <span class="backs">
                <i v-for="i in d.dice.pet" :key="i" class="back" />
              </span>
            </div>
            <div class="muted">剩 {{ d.dice.pet }} 颗</div>
          </div>
          <div class="mid">
            <div class="muted">第 {{ d.round }} 轮</div>
            <div class="muted">场上共 {{ d.total }} 颗</div>
          </div>
          <div class="side" :class="{ active: d.turn === 'player' && d.phase === 'bid' && !showEnd }">
            <div class="side-name">你</div>
            <div class="row">
              <game-icon v-for="(n, i) in d.hand" :key="i" :name="'die' + n" :size="40" />
            </div>
            <div class="muted">剩 {{ d.dice.player }} 颗</div>
          </div>
        </div>

        <div class="bids">
          <div v-if="!d.bids.length" class="muted">这一轮由{{ who(d.turn) }}先报数</div>
          <div v-for="(b, i) in d.bids" :key="i" class="bid" :class="{ latest: i === d.bids.length - 1 }">
            {{ who(b.side) }}：我认为场上有 {{ b.count }} 个 {{ b.face }} 点
            <game-icon :name="'die' + b.face" :size="18" />
            <span v-if="b.say" class="say">—— “{{ b.say }}”</span>
          </div>
        </div>

        <div v-if="myTurn" class="actions">
          <template v-if="!bidding">
            <n-space>
              <n-button type="primary" size="large" :disabled="games.busy || !d.can_raise" @click="openBid">报数</n-button>
              <n-button size="large" :disabled="games.busy || !d.can_call" @click="callIt">喊开</n-button>
            </n-space>
            <div v-if="!d.can_call" class="muted hint">还没有人报数</div>
            <div v-if="!d.can_raise" class="muted hint">已经报到最大了，只能喊开</div>
          </template>
          <template v-else>
            <div class="fill">
              我认为场上有
              <n-input-number v-model:value="bidCount" :min="1" :max="d.total" size="small" style="width: 100px" />
              个
              <n-input-number v-model:value="bidFace" :min="1" :max="6" size="small" style="width: 100px" />
              点
              <n-button type="primary" :disabled="games.busy || !!bidError" @click="submitBid">确定</n-button>
              <n-button :disabled="games.busy" @click="bidding = false">取消</n-button>
            </div>
            <div v-if="bidError" class="muted hint">{{ bidError }}</div>
          </template>
        </div>

        <div v-if="petTurn && !showReveal" class="actions">
          <template v-if="!petFailed">
            <n-spin size="small" />
            <span class="muted">{{ petName }}正在想…</span>
          </template>
          <template v-else>
            <span class="muted">{{ petName }}这一步没有成功</span>
            <n-button size="small" :disabled="games.busy" @click="retryPet">重试</n-button>
          </template>
        </div>

        <div v-if="!showEnd" class="footer">
          <n-popconfirm @positive-click="quit">
            <template #trigger><n-button size="small" quiet :disabled="games.busy">放弃这一局</n-button></template>
            放弃后这一局不计入战绩，确定吗？
          </n-popconfirm>
        </div>
      </template>
    </template>

    <glass-modal :show="showReveal" sticky title="开！">
      <div v-if="revealSource" class="reveal">
        <div>{{ who(revealSource.caller) }}喊开了“{{ revealSource.bid.count }} 个 {{ revealSource.bid.face }} 点”</div>
        <div v-for="side in (['pet', 'player'] as const)" :key="side" class="rev-row">
          <span class="rev-name">{{ who(side) }}</span>
          <span v-for="(n, i) in revealSource.hands[side]" :key="i" class="rd" :class="{ hit: n === revealSource.bid.face }">
            <game-icon :name="'die' + n" :size="34" />
          </span>
        </div>
        <div>场上一共 {{ revealSource.total }} 个 {{ revealSource.bid.face }} 点</div>
        <div class="verdict">{{ who(revealSource.loser) }}掉一颗骰子</div>
        <div v-if="revealSource.say" class="say">{{ revealSource.say }}</div>
        <n-button type="primary" style="margin-top: 14px" :disabled="games.busy" @click="nextRound">下一轮</n-button>
      </div>
    </glass-modal>

    <game-over
      :show="showEnd"
      :outcome="d?.winner === 'player' ? 'win' : 'lose'"
      :title="d?.winner === 'player' ? '你赢了' : `${petName}赢了`"
      :end="d?.end ?? null"
      @again="start"
      @back="backToLobby"
    >
      <div v-if="revealSource" class="reveal">
        <div v-for="side in (['pet', 'player'] as const)" :key="side" class="rev-row">
          <span class="rev-name">{{ who(side) }}</span>
          <span v-for="(n, i) in revealSource.hands[side]" :key="i" class="rd" :class="{ hit: n === revealSource.bid.face }">
            <game-icon :name="'die' + n" :size="30" />
          </span>
        </div>
        <div>场上一共 {{ revealSource.total }} 个 {{ revealSource.bid.face }} 点</div>
      </div>
      <div v-if="d" style="margin-top: 8px">共 {{ d.round }} 轮</div>
    </game-over>
  </div>
</template>

<style scoped>
.dice {
  max-width: 760px;
}
.record {
  margin-bottom: 8px;
  opacity: 0.75;
}
.rules {
  line-height: 1.8;
  opacity: 0.85;
}
.setup {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 14px;
}
.sides {
  display: flex;
  align-items: stretch;
  gap: 12px;
  flex-wrap: wrap;
}
.side {
  flex: 1;
  min-width: 220px;
  padding: 12px 14px;
  border: 1px solid color-mix(in srgb, currentColor 22%, transparent);
  border-radius: 10px;
  transition: box-shadow 0.2s, border-color 0.2s;
}
.side.active {
  border-color: v-bind(accent);
  box-shadow: 0 0 0 2px color-mix(in srgb, v-bind(accent) 35%, transparent);
}
.side-name {
  font-weight: 700;
  margin-bottom: 6px;
}
.row {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  min-height: 44px;
}
.backs {
  display: inline-flex;
  gap: 4px;
}
.back {
  width: 16px;
  height: 16px;
  border-radius: 3px;
  background: color-mix(in srgb, currentColor 28%, transparent);
  border: 1px solid color-mix(in srgb, currentColor 40%, transparent);
}
.mid {
  display: flex;
  flex-direction: column;
  justify-content: center;
  text-align: center;
}
.muted {
  opacity: 0.65;
  font-size: 0.92em;
}
.bids {
  margin-top: 14px;
  padding: 10px 14px;
  border-radius: 10px;
  background: color-mix(in srgb, currentColor 6%, transparent);
  line-height: 2;
}
.bid {
  opacity: 0.8;
}
.bid.latest {
  font-weight: 700;
  opacity: 1;
}
.say {
  margin-left: 6px;
  opacity: 0.85;
  font-weight: 400;
}
.actions {
  margin-top: 14px;
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.hint {
  width: 100%;
  /* Sized and dimmed by .muted; undo the console-wide .hint on top of it. */
  font-size: 0.92em;
  opacity: 0.65;
}
.fill {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.footer {
  margin-top: 18px;
}
.reveal {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 10px;
  line-height: 1.7;
}
.rev-row {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
}
.rev-name {
  min-width: 56px;
  text-align: right;
  margin-right: 8px;
  opacity: 0.85;
}
.rd {
  display: inline-flex;
  padding: 2px;
  border-radius: 6px;
  opacity: 0.7;
}
.rd.hit {
  opacity: 1;
  background: color-mix(in srgb, v-bind(accent) 45%, transparent);
  outline: 1px solid v-bind(accent);
}
.verdict {
  font-weight: 700;
  font-size: 1.15em;
}
</style>
