<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { NAlert, NButton, NCollapse, NCollapseItem, NPopconfirm, NProgress, NSpin, NSwitch } from 'naive-ui'
import { cardFace, games, petName, useGameRun, who } from '../games'
import { ui } from '../store'
import GameOver from './GameOver.vue'

const emit = defineEmits<{ back: [] }>()
const run = useGameRun()
const accent = computed(() => ui.value.accent)

const a = computed(() => games.state?.auction ?? null)
const record = computed(() => games.state?.records.auction ?? { player: 0, pet: 0, draw: 0 })
const llmAvailable = computed(() => games.state?.llm_available ?? false)
const useLlm = ref(true)
watch(
  llmAvailable,
  (v) => {
    if (!v) useLlm.value = false
  },
  { immediate: true }
)

const picked = ref<number | null>(null)
watch(
  () => a.value?.history.length,
  () => {
    picked.value = null
  }
)

async function start(): Promise<void> {
  picked.value = null
  await run('/api/games/auction/start', { use_llm: useLlm.value && llmAvailable.value })
}
async function play(): Promise<void> {
  if (picked.value === null) return
  const card = picked.value
  if (await run('/api/games/auction/play', { card })) picked.value = null
}
async function quit(): Promise<void> {
  await run('/api/games/auction/quit')
}
async function backToLobby(): Promise<void> {
  await run('/api/games/auction/quit')
  emit('back')
}

const potSum = computed(() => (a.value ? a.value.pot.reduce((s, n) => s + n, 0) : 0))
const last = computed(() => (a.value && a.value.history.length ? a.value.history[a.value.history.length - 1] : null))
const lastText = computed(() => {
  const l = last.value
  if (!l) return ''
  const head = `你出 ${cardFace(l.player)} · ${petName.value} 出 ${cardFace(l.pet)}`
  if (l.winner === null) return `${head} → 打平，奖励留在桌上`
  return `${head} → ${who(l.winner)}拿走 ${l.prizes.reduce((s, n) => s + n, 0)} 分`
})
const pct = (v: number): number => (a.value ? Math.min(100, (v / a.value.win_score) * 100) : 0)
const winnerOf = (w: 'player' | 'pet' | null): string => (w === null ? '平' : who(w))
const outcome = computed<'win' | 'lose' | 'draw'>(() => (a.value?.result === 'player' ? 'win' : a.value?.result === 'pet' ? 'lose' : 'draw'))
const overTitle = computed(() => (a.value?.result === 'player' ? '你赢了' : a.value?.result === 'pet' ? `${petName.value}赢了` : '平局'))
</script>

<template>
  <div class="auction">
    <template v-if="!a">
      <div class="record">战绩：胜 {{ record.player }} · 负 {{ record.pet }} · 平 {{ record.draw }}</div>
      <n-collapse>
        <n-collapse-item title="规则说明" name="rules">
          <div class="rules">
            三种花色各 A~K（A=1 … K=13）。你和{{ petName }}各持一套当出价牌，第三套是奖励牌，洗乱后每轮翻开一张，牌面多少就值多少分。双方同时暗出一张牌，同时亮出，大的拿走桌上的奖励分，打出的牌双方都作废。出的牌一样大则没人得分，奖励牌留在桌上，和下一张一起归下一轮的赢家。共 13 轮，先到 45 分的人赢，打完没人到 45 就比分数。
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
      <div class="scores">
        <div class="score">
          <div>你 {{ a.scores.player }} 分</div>
          <n-progress type="line" :percentage="pct(a.scores.player)" :show-indicator="false" :height="10" :color="accent" />
        </div>
        <div class="score">
          <div>{{ petName }} {{ a.scores.pet }} 分</div>
          <n-progress type="line" :percentage="pct(a.scores.pet)" :show-indicator="false" :height="10" :color="accent" />
        </div>
        <div class="muted">先到 {{ a.win_score }} 分获胜</div>
        <div class="muted">第 {{ a.round }} / {{ a.rounds }} 轮 · 未翻开的奖励牌 {{ a.left }} 张</div>
      </div>

      <div class="table">
        <div v-if="a.pot.length > 1" class="muted">上一轮打平，累计</div>
        <div class="pot">
          <span v-for="(n, i) in a.pot" :key="i" class="card prize big">{{ cardFace(n) }}</span>
        </div>
        <div class="reward">本轮奖励 {{ potSum }} 分</div>
      </div>

      <div v-if="last" class="last">
        <div class="flip-row" :key="a.history.length">
          <span class="card mine flip">{{ cardFace(last.player) }}</span>
          <span class="card theirs flip d2">{{ cardFace(last.pet) }}</span>
        </div>
        <div>{{ lastText }}</div>
        <div v-if="last.say" class="muted">{{ petName }}：“{{ last.say }}”</div>
      </div>

      <div class="block-title">{{ petName }}还剩的牌</div>
      <div class="hand">
        <span v-for="n in a.pet_hand" :key="n" class="card theirs small">{{ cardFace(n) }}</span>
      </div>

      <div class="block-title">你的手牌</div>
      <div class="hand">
        <button
          v-for="n in a.hand"
          :key="n"
          class="card mine pick"
          :class="{ sel: picked === n }"
          :disabled="games.busy"
          @click="picked = n"
        >
          {{ cardFace(n) }}
        </button>
      </div>
      <div class="actions">
        <template v-if="games.busy">
          <n-spin size="small" />
          <span class="muted">已扣下你的牌，等{{ petName }}出牌后同时亮出…</span>
        </template>
        <n-button v-else type="primary" :disabled="picked === null" @click="play">出这张牌{{ picked !== null ? '：' + cardFace(picked) : '' }}</n-button>
      </div>

      <n-collapse v-if="a.history.length" style="margin-top: 14px">
        <n-collapse-item title="历史记录" name="history">
          <table class="hist">
            <thead>
              <tr><th>轮次</th><th>奖励</th><th>你出</th><th>{{ petName }}出</th><th>谁拿走</th></tr>
            </thead>
            <tbody>
              <tr v-for="h in a.history" :key="h.round">
                <td>{{ h.round }}</td>
                <td>{{ h.prizes.map(cardFace).join(' + ') }}（{{ h.prizes.reduce((s, n) => s + n, 0) }}）</td>
                <td>{{ cardFace(h.player) }}</td>
                <td>{{ cardFace(h.pet) }}</td>
                <td>{{ winnerOf(h.winner) }}</td>
              </tr>
            </tbody>
          </table>
        </n-collapse-item>
      </n-collapse>

      <div v-if="a.result === null" class="footer">
        <n-popconfirm @positive-click="quit">
          <template #trigger><n-button size="small" quiet :disabled="games.busy">放弃这一局</n-button></template>
          放弃后这一局不计入战绩，确定吗？
        </n-popconfirm>
      </div>
    </template>

    <game-over :show="!!a && a.result !== null" :outcome="outcome" :title="overTitle" :end="a?.end ?? null" @again="start" @back="backToLobby">
      <div v-if="a">你 {{ a.scores.player }} 分 · {{ petName }} {{ a.scores.pet }} 分 · 共 {{ a.history.length }} 轮</div>
    </game-over>
  </div>
</template>

<style scoped>
.auction {
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
.muted {
  opacity: 0.65;
  font-size: 0.92em;
}
.scores {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px 20px;
}
.table {
  margin: 16px 0;
  padding: 14px;
  text-align: center;
  border-radius: 12px;
  background: color-mix(in srgb, currentColor 6%, transparent);
}
.pot {
  display: flex;
  gap: 10px;
  justify-content: center;
  flex-wrap: wrap;
  margin: 8px 0;
}
.reward {
  font-weight: 700;
}
.card {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  box-sizing: border-box;
  width: 48px;
  height: 68px;
  font-size: 1.3em;
  font-weight: 700;
  color: inherit;
  background: color-mix(in srgb, currentColor 5%, transparent);
  border-radius: 8px;
  font-family: inherit;
}
.card.mine {
  border: 2px solid currentColor;
}
.card.theirs {
  border: 2px dashed currentColor;
}
.card.prize {
  border: 5px double currentColor;
  font-weight: 800;
}
.card.big {
  width: 72px;
  height: 100px;
  font-size: 2em;
}
.card.small {
  width: 34px;
  height: 48px;
  font-size: 1em;
  opacity: 0.85;
}
.hand {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin: 6px 0 10px;
}
.pick {
  cursor: pointer;
  transition: transform 0.15s, box-shadow 0.15s;
}
.pick:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}
.pick.sel {
  transform: translateY(-10px);
  border-color: v-bind(accent);
  color: v-bind(accent);
  box-shadow: 0 6px 14px color-mix(in srgb, v-bind(accent) 35%, transparent);
}
.block-title {
  margin-top: 12px;
  font-weight: 700;
}
.actions {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 34px;
  margin-top: 8px;
}
.last {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  margin-bottom: 8px;
}
.flip-row {
  display: flex;
  gap: 16px;
}
.flip {
  animation: flip-in 0.5s ease both;
}
.flip.d2 {
  animation-delay: 0.15s;
}
@keyframes flip-in {
  from {
    transform: rotateY(90deg);
    opacity: 0;
  }
}
.hist {
  width: 100%;
  border-collapse: collapse;
  text-align: left;
}
.hist th,
.hist td {
  padding: 4px 8px;
  border-bottom: 1px solid color-mix(in srgb, currentColor 15%, transparent);
}
.footer {
  margin-top: 18px;
}
@media (prefers-reduced-motion: reduce) {
  .flip {
    animation: none;
  }
  .pick {
    transition: none;
  }
}
</style>
