<script setup lang="ts">
import { onBeforeUnmount, watch } from 'vue'

/**
 * Semi-transparent frosted-glass overlay over the whole console window, with a card in the middle.
 * `autoClose` (ms): emits `close` by itself after that long; a click on the backdrop also emits `close`
 * unless `sticky`.
 */
const props = defineProps<{ show: boolean; title?: string; subtitle?: string; autoClose?: number; sticky?: boolean }>()
const emit = defineEmits<{ close: [] }>()

let timer = 0
watch(
  () => props.show,
  (on) => {
    clearTimeout(timer)
    if (on && props.autoClose) timer = window.setTimeout(() => emit('close'), props.autoClose)
  },
  { immediate: true }
)
onBeforeUnmount(() => clearTimeout(timer))
</script>

<template>
  <teleport to="body">
    <transition name="glass">
      <div v-if="show" class="glass-mask" @click.self="!sticky && emit('close')">
        <div class="glass-card">
          <div v-if="title" class="glass-title">{{ title }}</div>
          <div v-if="subtitle" class="glass-sub">{{ subtitle }}</div>
          <slot />
        </div>
      </div>
    </transition>
  </teleport>
</template>

<style scoped>
.glass-mask {
  position: fixed;
  inset: 0;
  z-index: 3000;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(20, 20, 28, 0.35);
  backdrop-filter: blur(10px) saturate(1.2);
}
.glass-card {
  min-width: 300px;
  max-width: min(560px, 86vw);
  max-height: 86vh;
  overflow: auto;
  padding: 28px 36px;
  text-align: center;
  color: #fff;
  background: rgba(40, 40, 52, 0.55);
  border: 1px solid rgba(255, 255, 255, 0.28);
  border-radius: 18px;
  box-shadow: 0 18px 50px rgba(0, 0, 0, 0.35);
  backdrop-filter: blur(18px);
}
.glass-title {
  font-size: 2em;
  font-weight: 700;
  letter-spacing: 0.08em;
}
.glass-sub {
  margin-top: 8px;
  line-height: 1.7;
  opacity: 0.85;
}
.glass-enter-active,
.glass-leave-active {
  transition: opacity 0.25s;
}
.glass-enter-active .glass-card {
  animation: glass-in 0.3s cubic-bezier(0.2, 0.8, 0.3, 1.2);
}
.glass-enter-from,
.glass-leave-to {
  opacity: 0;
}
@keyframes glass-in {
  from {
    transform: scale(0.85);
  }
}
@media (prefers-reduced-motion: reduce) {
  .glass-enter-active .glass-card {
    animation: none;
  }
}
</style>
