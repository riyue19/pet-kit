<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { NAlert, NButton, NCollapse, NCollapseItem, NInput, NPopconfirm, NRadio, NRadioGroup, NSpace, NSpin } from 'naive-ui'
import { games, petName, useGameRun } from '../games'
import { ui } from '../store'
import GameOver from './GameOver.vue'

const emit = defineEmits<{ back: [] }>()
const run = useGameRun()
const accent = computed(() => ui.value.accent)

const t = computed(() => games.state?.taboo ?? null)
const record = computed(() => games.state?.records.taboo ?? { player: 0, pet: 0, draw: 0 })
const llmAvailable = computed(() => games.state?.llm_available ?? false)
const describer = ref<'player' | 'pet' | 'random'>('random')
const text = ref('')
const listEl = ref<HTMLElement | null>(null)

async function start(): Promise<void> {
  text.value = ''
  await run('/api/games/taboo/start', { describer: describer.value })
}
async function say(): Promise<void> {
  const s = text.value.trim()
  if (!s || games.busy) return
  if (await run('/api/games/taboo/say', { text: s })) text.value = ''
}
async function quit(): Promise<void> {
  await run('/api/games/taboo/quit')
}
async function backToLobby(): Promise<void> {
  await run('/api/games/taboo/quit')
  emit('back')
}

watch(
  () => t.value?.turns.length,
  async () => {
    await nextTick()
    if (listEl.value) listEl.value.scrollTop = listEl.value.scrollHeight
  },
  { immediate: true, flush: 'post' }
)

const iDescribe = computed(() => t.value?.describer === 'player')
const placeholder = computed(() => (iDescribe.value ? '写下你的描述…' : '你猜是什么？'))
</script>

<template>
  <div class="taboo">
    <template v-if="!t">
      <div class="record">战绩：猜中 {{ record.player }} · 没猜出 {{ record.draw + record.pet }}</div>
      <n-collapse>
        <n-collapse-item title="规则说明" name="rules">
          <div class="rules">
            系统随机出一个目标词和几个禁忌词。拿到词的一方来描述，描述里不能出现目标词和禁忌词；另一方来猜，10 轮之内猜中就算一起赢。
          </div>
        </n-collapse-item>
      </n-collapse>
      <div class="setup">
        <n-radio-group v-model:value="describer">
          <n-space>
            <n-radio value="random">随机</n-radio>
            <n-radio value="player">我来描述，{{ petName }}猜</n-radio>
            <n-radio value="pet">{{ petName }}描述，我来猜</n-radio>
          </n-space>
        </n-radio-group>
      </div>
      <n-alert v-if="!llmAvailable" type="warning" :show-icon="false" style="margin-top: 10px">这个游戏需要聊天用的 LLM</n-alert>
      <n-button type="primary" size="large" style="margin-top: 16px" :loading="games.busy" :disabled="games.busy || !llmAvailable" @click="start">开始</n-button>
    </template>

    <template v-else>
      <div class="head">
        <span>第 {{ t.round }} / {{ t.rounds }} 轮</span>
        <span class="muted">{{ iDescribe ? `你来描述，${petName}来猜` : `${petName}来描述，你来猜` }}</span>
      </div>

      <div v-if="iDescribe && t.word !== null" class="wordcard">
        <div class="word">{{ t.word }}</div>
        <div class="tags">
          <span v-for="w in t.taboo ?? []" :key="w" class="tag">{{ w }}</span>
        </div>
        <div class="muted">描述里不能出现目标词和这些词，否则会被拦下</div>
      </div>
      <div v-else-if="!iDescribe" class="muted" style="margin-top: 8px">答案是 {{ t.word_chars }} 个字</div>

      <div ref="listEl" class="list">
        <div v-for="(m, i) in t.turns" :key="i" class="msg" :class="m.side === 'player' ? 'right' : 'left'">
          <div class="bubble">
            <span class="kind">{{ m.kind === 'clue' ? '描述' : '猜' }}</span>
            {{ m.text }}
            <span v-if="m.kind === 'guess' && m.ok === false" class="kind">没猜中</span>
            <span v-if="m.kind === 'guess' && m.ok === true" class="kind ok">猜中了</span>
          </div>
        </div>
      </div>

      <div v-if="t.result === null" class="input">
        <n-input
          v-model:value="text"
          :maxlength="200"
          :placeholder="placeholder"
          :disabled="games.busy"
          @keyup.enter="say"
        />
        <n-button type="primary" :disabled="games.busy || !text.trim()" @click="say">发送</n-button>
      </div>
      <div v-if="games.busy" class="thinking">
        <n-spin size="small" />
        <span class="muted">{{ petName }}正在想…</span>
      </div>

      <div v-if="t.result === null" class="footer">
        <n-popconfirm @positive-click="quit">
          <template #trigger><n-button size="small" quiet :disabled="games.busy">放弃这一局</n-button></template>
          放弃后这一局不计入战绩，确定吗？
        </n-popconfirm>
      </div>
    </template>

    <game-over
      :show="!!t && t.result !== null"
      :outcome="t?.result === 'player' ? 'win' : 'lose'"
      :title="t?.result === 'player' ? '猜中了' : '没猜出来'"
      :end="t?.end ?? null"
      @again="start"
      @back="backToLobby"
    >
      <div v-if="t" class="over">
        <div>答案：{{ t.word }}</div>
        <div v-if="t.taboo">禁忌词：{{ t.taboo.join('、') }}</div>
        <div>用了 {{ t.round }} 轮</div>
      </div>
    </game-over>
  </div>
</template>

<style scoped>
.taboo {
  max-width: 720px;
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
  margin-top: 14px;
}
.muted {
  opacity: 0.65;
  font-size: 0.92em;
}
.head {
  display: flex;
  gap: 16px;
  align-items: baseline;
  font-weight: 700;
}
.wordcard {
  margin-top: 10px;
  padding: 14px;
  text-align: center;
  border: 1px solid color-mix(in srgb, currentColor 22%, transparent);
  border-radius: 12px;
  background: color-mix(in srgb, currentColor 5%, transparent);
}
.word {
  font-size: 2em;
  font-weight: 800;
  letter-spacing: 0.1em;
  color: v-bind(accent);
}
.tags {
  display: flex;
  gap: 8px;
  justify-content: center;
  flex-wrap: wrap;
  margin: 10px 0;
}
.tag {
  padding: 2px 10px;
  border-radius: 999px;
  text-decoration: line-through;
  border: 1px solid color-mix(in srgb, currentColor 35%, transparent);
}
.list {
  margin-top: 12px;
  height: 320px;
  overflow-y: auto;
  padding: 10px;
  border-radius: 10px;
  background: color-mix(in srgb, currentColor 5%, transparent);
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.msg {
  display: flex;
}
.msg.right {
  justify-content: flex-end;
}
.bubble {
  max-width: 78%;
  padding: 8px 12px;
  line-height: 1.6;
  border-radius: 12px;
  word-break: break-word;
  background: color-mix(in srgb, currentColor 10%, transparent);
}
.right .bubble {
  background: color-mix(in srgb, v-bind(accent) 28%, transparent);
}
.kind {
  display: inline-block;
  margin: 0 4px;
  padding: 0 6px;
  font-size: 0.78em;
  border-radius: 4px;
  border: 1px solid color-mix(in srgb, currentColor 35%, transparent);
}
.kind.ok {
  border-color: v-bind(accent);
  color: v-bind(accent);
}
.input {
  display: flex;
  gap: 8px;
  margin-top: 10px;
}
.thinking {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
}
.footer {
  margin-top: 16px;
}
.over {
  display: flex;
  flex-direction: column;
  gap: 6px;
  line-height: 1.7;
}
</style>
