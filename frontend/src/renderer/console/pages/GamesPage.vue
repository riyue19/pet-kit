<script setup lang="ts">
import { computed, onMounted, ref, watch, type Component } from 'vue'
import { NAlert, NButton, NCard, NSpace, NSpin, NSwitch, NTag } from 'naive-ui'
import type { GameKey } from '../../../shared/backend'
import BackendOffline from '../components/BackendOffline.vue'
import PageHead from '../components/PageHead.vue'
import { GAME_INFO, GAME_KEYS, games, loadGames, petName, useGameRun } from '../games'
import { GameIcon } from '../games/GameIcon'
import RouletteGame from '../games/RouletteGame.vue'
import RpsGame from '../games/RpsGame.vue'
import DiceGame from '../games/DiceGame.vue'
import AuctionGame from '../games/AuctionGame.vue'
import TabooGame from '../games/TabooGame.vue'
import FishingGame from '../games/FishingPage.vue'
import { fishing, loadFishing } from '../fishing'

// Idle fishing is not a versus game (no rounds, no win/lose record), so it is a view of its own, not a GameKey.
type View = GameKey | 'fishing'
const VIEWS: Record<View, Component> = { roulette: RouletteGame, rps: RpsGame, dice: DiceGame, auction: AuctionGame, taboo: TabooGame, fishing: FishingGame }
const FISHING_INFO = { name: '挂机钓鱼', desc: '自己抛竿，偶尔叫你帮忙' }
const viewName = (v: View): string => (v === 'fishing' ? FISHING_INFO.name : GAME_INFO[v].name)

const run = useGameRun()
// null = the lobby.
const current = ref<View | null>(null)

onMounted(() => {
  void loadGames()
  void loadFishing()
})

const s = computed(() => games.state)
const fishingText = computed(() => {
  const f = fishing.state
  if (!f) return '还没开始'
  return `图鉴 ${f.found}/${f.species.length} · 金币 ${f.coins}`
})
const inProgress = (k: View): boolean => {
  if (k === 'fishing') return false
  const g = s.value?.[k]
  return !!g && ('winner' in g ? g.winner === null : g.result === null)
}
function recordText(k: GameKey): string {
  const r = s.value?.records?.[k]
  if (!r || r.player + r.pet + r.draw === 0) return '还没玩过'
  if (k === 'taboo') return `猜中 ${r.player} · 没猜中 ${r.draw}`
  return `你 ${r.player} · ${petName.value} ${r.pet} · 平 ${r.draw}`
}
const setSetting = (key: 'voice' | 'invite', v: boolean): void => void run('/api/games/settings', { [key]: v }, 'PUT')

// The pet's invitation (accepted in the dialog on the pet side) brings the lobby to the front.
watch(
  () => s.value?.invited,
  (k) => {
    if (k && current.value !== null && !inProgress(current.value)) current.value = null
  }
)
</script>

<template>
  <page-head title="小游戏">
    <template v-if="current">
      <span class="crumb">/ {{ viewName(current) }}</span>
      <n-button size="small" quaternary class="back" @click="current = null">返回大厅</n-button>
    </template>
  </page-head>
  <n-spin :show="games.loading && !games.loaded">
    <backend-offline v-if="games.offline || (!games.loaded && games.error)" :error="games.error" :loading="games.loading" @retry="loadGames" />
    <template v-else-if="games.loaded && s">
      <component :is="VIEWS[current]" v-if="current" :key="current" @back="current = null" />

      <n-space v-else vertical :size="20">
        <n-alert v-if="!s.llm_available" type="info" :show-icon="false">还没有指定聊天用的 LLM：{{ petName }}会用内置策略出招，也不会说话；「禁忌词」玩不了。</n-alert>
        <n-alert v-if="s.invited" type="success" :show-icon="false">{{ petName }}邀请你玩「{{ GAME_INFO[s.invited].name }}」。</n-alert>

        <div class="lobby">
          <button v-for="k in GAME_KEYS" :key="k" type="button" class="tile" :class="{ invited: s.invited === k }" @click="current = k">
            <game-icon :name="k" :size="72" />
            <span class="tile-name">{{ GAME_INFO[k].name }}</span>
            <span class="tile-desc">{{ GAME_INFO[k].desc }}</span>
            <span class="tile-record">{{ recordText(k) }}</span>
            <n-tag v-if="inProgress(k)" class="tile-tag" size="small" type="warning" round :bordered="false">进行中</n-tag>
            <n-tag v-else-if="s.invited === k" class="tile-tag" size="small" type="success" round :bordered="false">{{ petName }}想玩</n-tag>
          </button>
          <button type="button" class="tile" @click="current = 'fishing'">
            <game-icon name="fishing" :size="72" />
            <span class="tile-name">{{ FISHING_INFO.name }}</span>
            <span class="tile-desc">{{ FISHING_INFO.desc }}</span>
            <span class="tile-record">{{ fishingText }}</span>
            <n-tag v-if="fishing.state?.enabled" class="tile-tag" size="small" type="info" round :bordered="false">挂机中</n-tag>
          </button>
        </div>

        <n-card title="设置" size="small">
          <n-space vertical :size="12">
            <n-space align="center">
              <n-switch :value="s.settings.voice" :disabled="games.busy" @update:value="(v: boolean) => setSetting('voice', v)" />
              <span>游戏里的对话开语音（{{ petName }}在游戏里说的话也念出来；关着只显示文字）</span>
            </n-space>
            <n-space align="center">
              <n-switch :value="s.settings.invite" :disabled="games.busy" @update:value="(v: boolean) => setSetting('invite', v)" />
              <span>允许{{ petName }}主动邀请你玩小游戏（会弹窗询问，你同意才打开）</span>
            </n-space>
            <span class="hint">
              游戏里说的话是临时的，不进聊天记录，也不进记忆；{{ petName }}只记得玩过什么、谁赢了几局。
              <template v-if="s.status_on">状态栏开着：你赢一局好感度 +0.5（{{ petName }}邀请的那一局赢了 +1）；输了或平局由{{ petName }}决定加不加。</template>
              <template v-else>状态栏关着，输赢不影响好感度。</template>
            </span>
          </n-space>
        </n-card>
      </n-space>
    </template>
  </n-spin>
</template>

<style scoped>
.crumb {
  font-size: 1.1em;
  opacity: 0.7;
}
.back {
  margin-left: auto;
}
.hint {
  line-height: 1.7;
}
.lobby {
  display: grid;
  /* Three across when there is room (six tiles = two rows), two in a narrow window. */
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  max-width: 800px;
  gap: 20px;
}
.tile {
  all: unset;
  position: relative;
  box-sizing: border-box;
  aspect-ratio: 1 / 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 16px;
  text-align: center;
  cursor: pointer;
  border: 2px solid currentColor;
  border-radius: 14px;
  background: rgba(128, 128, 128, 0.06);
  transition: transform 0.15s, background 0.15s, box-shadow 0.15s;
}
.tile:hover,
.tile:focus-visible {
  transform: translateY(-3px);
  background: rgba(128, 128, 128, 0.14);
  box-shadow: 0 6px 0 0 currentColor;
}
.tile:active {
  transform: translateY(1px);
  box-shadow: 0 2px 0 0 currentColor;
}
.tile.invited {
  border-style: dashed;
}
.tile-name {
  margin-top: 6px;
  font-size: 1.25em;
  font-weight: 700;
  letter-spacing: 0.05em;
}
.tile-desc {
  font-size: 0.9em;
  opacity: 0.7;
}
.tile-record {
  font-size: 0.82em;
  opacity: 0.5;
  font-variant-numeric: tabular-nums;
}
.tile-tag {
  position: absolute;
  top: 12px;
  right: 12px;
}
</style>
