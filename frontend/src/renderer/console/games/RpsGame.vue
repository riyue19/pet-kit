<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { NButton, NCollapse, NCollapseItem, NInput, NPopconfirm, NSpin, NStep, NSteps, NSwitch } from 'naive-ui'
import type { GameSide, Gesture } from '../../../shared/backend'
import { ui } from '../store'
import { games, GESTURE_NAME, petName, useGameRun } from '../games'
import { GameIcon } from './GameIcon'
import GlassModal from './GlassModal.vue'
import GameOver from './GameOver.vue'

const emit = defineEmits<{ back: [] }>()
const run = useGameRun()
const accent = computed(() => ui.value.accent)

const g = computed(() => games.state?.rps ?? null)
const record = computed(() => games.state?.records.rps ?? { player: 0, pet: 0, draw: 0 })

const timers = new Set<number>()
function later(fn: () => void, ms: number): number {
  const id = window.setTimeout(() => {
    timers.delete(id)
    fn()
  }, ms)
  timers.add(id)
  return id
}
onBeforeUnmount(() => {
  timers.forEach((id) => clearTimeout(id))
  timers.clear()
})

// --- setup ---
const useLlm = ref(true)
watch(
  () => games.state?.llm_available,
  (ok) => {
    if (ok === false) useLlm.value = false
  },
  { immediate: true }
)
async function onStart(): Promise<void> {
  await run('/api/games/rps/start', { use_llm: useLlm.value })
}

// --- round modal / talk state ---
const modalStep = ref<number | null>(null)
const selected = ref<Gesture | null>(null)
const picking = ref(false)
const talking = ref(g.value?.step === 3 && g.value.talk_left > 0)
const talkNote = ref('')
const showOver = ref(g.value?.step === 4)
const text = ref('')
const chatting = ref(false)
const chatBox = ref<HTMLElement | null>(null)

const SUBTITLE: Record<number, string> = {
  1: '各选一个手势，同时亮出',
  2: '再选一个不同的手势，同时亮出',
  3: '先聊几句互相试探，再从亮过的两个手势里暗选一个出招'
}

let prevStep = g.value?.step ?? 0
watch(
  () => g.value?.step ?? 0,
  (step) => {
    const was = prevStep
    prevStep = step
    selected.value = null
    if (step === 0) {
      showOver.value = false
      modalStep.value = null
      return
    }
    if (step === 4) {
      showOver.value = false
      later(() => (showOver.value = true), 1000)
      return
    }
    showOver.value = false
    if (step !== was) {
      modalStep.value = step
      if (step === 3) {
        talking.value = true
        talkNote.value = ''
      }
    }
  }
)
watch(
  () => g.value?.talk_left,
  (left) => {
    if (left === 0 && g.value?.step === 3 && talking.value) {
      talking.value = false
      talkNote.value = '5 轮对话用完了'
    }
  }
)
watch(
  () => g.value?.chat.length,
  async () => {
    await nextTick()
    if (chatBox.value) chatBox.value.scrollTop = chatBox.value.scrollHeight
  }
)

// --- cards ---
const SIDES: GameSide[] = ['player', 'pet']
function shown(side: GameSide, i: number): Gesture | null {
  return g.value?.picks[side][i] ?? null
}

// --- actions ---
async function confirmPick(): Promise<void> {
  if (!selected.value || picking.value) return
  picking.value = true
  try {
    await run('/api/games/rps/pick', { choice: selected.value })
  } finally {
    picking.value = false
    selected.value = null
  }
}
async function send(): Promise<void> {
  const t = text.value.trim()
  if (!t || chatting.value || games.busy) return
  chatting.value = true
  try {
    if (await run('/api/games/rps/chat', { text: t })) text.value = ''
  } finally {
    chatting.value = false
  }
}
async function quit(): Promise<void> {
  await run('/api/games/rps/quit')
}
async function again(): Promise<void> {
  await run('/api/games/rps/start', { use_llm: g.value?.use_llm ?? useLlm.value })
}
async function back(): Promise<void> {
  if (await run('/api/games/rps/quit')) emit('back')
}
</script>

<template>
  <div class="rps">
    <!-- setup -->
    <div v-if="!g" class="rps-setup">
      <n-collapse>
        <n-collapse-item title="规则说明" name="rules">
          <ul class="rps-rules">
            <li>第 1 局：双方各选一个手势，同时亮出。</li>
            <li>第 2 局：各选一个不同的手势，同时亮出。</li>
            <li>第 3 局：先有最多 5 轮对话（可以说真话也可以骗人，也可以跳过），然后各从自己亮过的两个里暗选一个作为最终出招，同时公布。</li>
            <li>石头赢剪刀、剪刀赢布、布赢石头。</li>
          </ul>
        </n-collapse-item>
      </n-collapse>
      <div class="rps-row">战绩：你 {{ record.player }} / {{ petName }} {{ record.pet }} / 平 {{ record.draw }}</div>
      <div class="rps-row">
        <n-switch v-model:value="useLlm" :disabled="games.state?.llm_available === false" />
        <span style="margin-left: 8px">让{{ petName }}自己思考并说话</span>
      </div>
      <div class="rps-row">
        <n-button type="primary" :loading="games.busy" :disabled="games.busy" @click="onStart">开始</n-button>
      </div>
    </div>

    <!-- play -->
    <div v-else class="rps-play">
      <n-steps :current="Math.min(g.step, 3)" :status="g.step === 4 ? 'finish' : 'process'">
        <n-step title="第 1 局" description="亮手势" />
        <n-step title="第 2 局" description="再亮一个" />
        <n-step title="第 3 局" description="最终出招" />
      </n-steps>

      <div v-if="g.step < 4 || !showOver" class="rps-boards">
        <div v-for="side in SIDES" :key="side" class="rps-line">
          <span class="rps-who">{{ side === 'player' ? '你亮出的' : `${petName}亮出的` }}</span>
          <div v-for="i in 2" :key="i" class="rps-cell" :class="{ on: !!shown(side, i - 1) }">
            <div v-if="shown(side, i - 1)" :key="shown(side, i - 1)!" class="rps-face">
              <game-icon :name="shown(side, i - 1)!" :size="44" />
              <span>{{ GESTURE_NAME[shown(side, i - 1)!] }}</span>
            </div>
            <span v-else class="rps-q">?</span>
          </div>
        </div>
      </div>

      <!-- step 1, 2 -->
      <div v-if="g.step === 1 || g.step === 2" class="rps-pick">
        <div class="rps-gestures">
          <button
            v-for="o in g.options"
            :key="o"
            type="button"
            class="rps-g"
            :class="{ sel: selected === o }"
            :disabled="picking || games.busy"
            @click="selected = o"
          >
            <game-icon :name="o" :size="40" />
            <span>{{ GESTURE_NAME[o] }}</span>
            <em v-if="selected === o">已选</em>
          </button>
        </div>
        <div class="rps-row">
          <n-button type="primary" :disabled="!selected || picking || games.busy" @click="confirmPick">确定出招</n-button>
          <span v-if="picking" class="rps-wait"><n-spin :size="16" /> 已锁定你的选择，等{{ petName }}选好后同时公布…</span>
        </div>
      </div>

      <!-- step 3 -->
      <div v-else-if="g.step === 3" class="rps-pick">
        <template v-if="talking">
          <div class="rps-title">出招前的对话 · 还剩 {{ g.talk_left }} / {{ g.talk_rounds }} 轮</div>
          <div class="rps-hint">可以说真话，也可以骗{{ petName }}；{{ petName }}也一样。</div>
        </template>
        <template v-else>
          <div v-if="talkNote" class="rps-hint">{{ talkNote }}</div>
          <div class="rps-title">从你亮过的两个手势里选一个作为最终出招</div>
        </template>

        <div v-if="g.chat.length || talking" ref="chatBox" class="rps-chat">
          <div v-for="(m, i) in g.chat" :key="i" class="rps-msg" :class="m.side">
            <span class="rps-bubble">{{ m.text }}</span>
          </div>
          <div v-if="!g.chat.length" class="rps-dim">还没有人说话。</div>
        </div>

        <template v-if="talking">
          <div class="rps-row">
            <n-input
              v-model:value="text"
              :maxlength="200"
              :disabled="chatting || games.busy"
              placeholder="说点什么…"
              style="max-width: 420px"
              @keydown.enter.prevent="send"
            />
            <n-button type="primary" :disabled="!text.trim() || chatting || games.busy" @click="send">发送</n-button>
            <span v-if="chatting" class="rps-wait"><n-spin :size="16" /> {{ petName }}正在回…</span>
          </div>
          <div class="rps-row">
            <n-button :disabled="chatting || games.busy" @click="talking = false">不聊了，直接出拳</n-button>
          </div>
        </template>
        <template v-else>
          <div class="rps-gestures">
            <button
              v-for="o in g.options"
              :key="o"
              type="button"
              class="rps-g"
              :class="{ sel: selected === o }"
              :disabled="picking || games.busy"
              @click="selected = o"
            >
              <game-icon :name="o" :size="40" />
              <span>{{ GESTURE_NAME[o] }}</span>
              <em v-if="selected === o">已选</em>
            </button>
          </div>
          <div class="rps-row">
            <n-button type="primary" :disabled="!selected || picking || games.busy" @click="confirmPick">确定出招</n-button>
            <n-button v-if="g.talk_left > 0" :disabled="picking || games.busy" @click="talking = true">再聊几句</n-button>
            <span v-if="picking" class="rps-wait"><n-spin :size="16" /> 已锁定你的选择，等{{ petName }}选好后同时公布…</span>
          </div>
        </template>
      </div>

      <!-- result reveal -->
      <div v-else-if="g.step === 4 && !showOver" class="rps-versus">
        <div class="rps-big">
          <game-icon v-if="g.final.player" :name="g.final.player" :size="88" />
          <span>{{ g.final.player ? GESTURE_NAME[g.final.player] : '' }}</span>
        </div>
        <b class="rps-vs">VS</b>
        <div class="rps-big">
          <game-icon v-if="g.final.pet" :name="g.final.pet" :size="88" />
          <span>{{ g.final.pet ? GESTURE_NAME[g.final.pet] : '' }}</span>
        </div>
      </div>

      <div v-if="g.step < 4" class="rps-foot">
        <n-popconfirm @positive-click="quit">
          <template #trigger><n-button size="small" quaternary :disabled="games.busy">放弃这一局</n-button></template>
          放弃后这一局不计战绩，确定吗？
        </n-popconfirm>
      </div>
    </div>

    <glass-modal
      :show="modalStep !== null"
      :title="`第 ${modalStep ?? ''} 局`"
      :subtitle="modalStep ? SUBTITLE[modalStep] : ''"
      :auto-close="1800"
      @close="modalStep = null"
    />

    <game-over
      v-if="g && g.step === 4"
      :show="showOver"
      :outcome="g.result === 'player' ? 'win' : g.result === 'pet' ? 'lose' : 'draw'"
      :title="g.result === 'player' ? '你赢了' : g.result === 'pet' ? `${petName}赢了` : '平局'"
      :end="g.end"
      @again="again"
      @back="back"
    >
      <div v-if="g.final.player && g.final.pet">你 {{ GESTURE_NAME[g.final.player] }} VS {{ petName }} {{ GESTURE_NAME[g.final.pet] }}</div>
      <div style="margin-top: 6px">战绩：你 {{ record.player }} / {{ petName }} {{ record.pet }} / 平 {{ record.draw }}</div>
    </game-over>
  </div>
</template>

<style scoped>
.rps-row {
  margin-top: 14px;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
}
.rps-rules {
  margin: 0;
  padding-left: 18px;
  line-height: 1.8;
}
.rps-boards {
  margin-top: 18px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.rps-line {
  display: flex;
  align-items: center;
  gap: 12px;
}
.rps-who {
  width: 110px;
  opacity: 0.75;
}
.rps-cell {
  width: 96px;
  height: 92px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 2px dashed color-mix(in srgb, currentColor 25%, transparent);
  border-radius: 12px;
  perspective: 400px;
}
.rps-cell.on {
  border-style: solid;
  border-color: v-bind(accent);
  background: color-mix(in srgb, v-bind(accent) 10%, transparent);
}
.rps-q {
  font-size: 1.8em;
  opacity: 0.4;
}
.rps-face {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  animation: rps-flip 0.6s ease-out;
}
.rps-pick {
  margin-top: 20px;
}
.rps-title {
  font-weight: 700;
}
.rps-hint,
.rps-dim {
  opacity: 0.7;
  margin-top: 4px;
}
.rps-gestures {
  margin-top: 12px;
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}
.rps-g {
  position: relative;
  min-width: 96px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  color: inherit;
  font: inherit;
  cursor: pointer;
  background: color-mix(in srgb, currentColor 5%, transparent);
  border: 2px solid color-mix(in srgb, currentColor 20%, transparent);
  border-radius: 12px;
}
.rps-g:hover:not(:disabled) {
  border-color: v-bind(accent);
}
.rps-g.sel {
  border-color: v-bind(accent);
  background: color-mix(in srgb, v-bind(accent) 18%, transparent);
}
.rps-g:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
.rps-g em {
  font-style: normal;
  font-size: 0.8em;
  color: v-bind(accent);
}
.rps-wait {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.rps-chat {
  margin-top: 10px;
  max-height: 220px;
  overflow-y: auto;
  padding: 8px 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  border-radius: 8px;
  background: color-mix(in srgb, currentColor 5%, transparent);
}
.rps-msg {
  display: flex;
}
.rps-msg.player {
  justify-content: flex-end;
}
.rps-bubble {
  max-width: 75%;
  padding: 6px 10px;
  border-radius: 10px;
  line-height: 1.5;
  word-break: break-word;
  background: color-mix(in srgb, currentColor 10%, transparent);
}
.rps-msg.player .rps-bubble {
  background: color-mix(in srgb, v-bind(accent) 22%, transparent);
}
.rps-versus {
  margin-top: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 40px;
}
.rps-big {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  font-size: 1.2em;
  animation: rps-flip 0.6s ease-out;
}
.rps-vs {
  font-size: 1.8em;
  letter-spacing: 0.1em;
  color: v-bind(accent);
}
.rps-foot {
  margin-top: 16px;
}
@keyframes rps-flip {
  from {
    transform: rotateY(90deg);
    opacity: 0;
  }
  to {
    transform: rotateY(0);
    opacity: 1;
  }
}
@media (prefers-reduced-motion: reduce) {
  .rps-face,
  .rps-big {
    animation: none;
  }
}
</style>
