<script setup lang="ts">
import { computed } from 'vue'
import { NButton, NSpace } from 'naive-ui'
import type { GameEnd } from '../../../shared/backend'
import { games, petName } from '../games'
import GlassModal from './GlassModal.vue'

/**
 * End of a match: frosted-glass card with the result, the pet's closing line and the affection reward;
 * confetti over the whole window when the player won. The default slot is for game-specific details.
 */
const props = defineProps<{ show: boolean; outcome: 'win' | 'lose' | 'draw'; title: string; end: GameEnd | null }>()
const emit = defineEmits<{ again: []; back: [] }>()

const COLORS = ['#ff7eb6', '#8c6cff', '#4aa8ff', '#31c48d', '#f0a020', '#ffffff']
// Fixed per mount: left %, delay s, duration s, size px, colour, drift px.
const pieces = Array.from({ length: 70 }, (_, i) => ({
  left: Math.random() * 100,
  delay: Math.random() * 1.8,
  dur: 2.6 + Math.random() * 2.2,
  size: 6 + Math.random() * 8,
  color: COLORS[i % COLORS.length],
  drift: (Math.random() - 0.5) * 160
}))
const reward = computed(() => props.end?.reward ?? null)
const signed = (v: number): string => `${v > 0 ? '+' : ''}${+v.toFixed(2)}`
</script>

<template>
  <teleport to="body">
    <div v-if="show && outcome === 'win'" class="confetti">
      <i
        v-for="(p, i) in pieces"
        :key="i"
        :style="{ left: p.left + '%', width: p.size + 'px', height: p.size * 0.6 + 'px', background: p.color, animationDelay: p.delay + 's', animationDuration: p.dur + 's', '--drift': p.drift + 'px' }"
      />
    </div>
  </teleport>
  <glass-modal :show="show" :title="title" sticky>
    <div class="go-body">
      <slot />
      <div v-if="end?.say" class="go-say">{{ petName }}：“{{ end.say }}”</div>
      <div v-if="reward" class="go-reward">{{ reward.name }} {{ signed(reward.delta) }} <span>（{{ reward.reason }}，现在 {{ +reward.value.toFixed(2) }}）</span></div>
      <n-space justify="center" style="margin-top: 20px">
        <n-button type="primary" :disabled="games.busy" @click="emit('again')">再来一局</n-button>
        <n-button :disabled="games.busy" ghost color="#ffffff" @click="emit('back')">返回大厅</n-button>
      </n-space>
    </div>
  </glass-modal>
</template>

<style scoped>
.go-body {
  margin-top: 12px;
}
.go-say {
  margin-top: 14px;
  font-size: 1.05em;
  line-height: 1.6;
}
.go-reward {
  margin-top: 10px;
  font-weight: 700;
  color: #ff9fca;
}
.go-reward span {
  font-weight: 400;
  opacity: 0.8;
}
.confetti {
  position: fixed;
  inset: 0;
  z-index: 3001;
  overflow: hidden;
  pointer-events: none;
}
.confetti i {
  position: absolute;
  top: -20px;
  border-radius: 1px;
  animation: confetti-fall linear infinite;
}
@keyframes confetti-fall {
  0% {
    transform: translate(0, 0) rotate(0);
    opacity: 1;
  }
  100% {
    transform: translate(var(--drift), 105vh) rotate(720deg);
    opacity: 0.9;
  }
}
@media (prefers-reduced-motion: reduce) {
  .confetti {
    display: none;
  }
}
</style>
