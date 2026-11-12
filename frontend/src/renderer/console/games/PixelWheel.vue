<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import type { GameSide } from '../../../shared/backend'
import { who } from '../games'

/**
 * Black-and-white pixel wheel that decides who goes first. The result comes from the backend (`first`);
 * the wheel only acts it out: black sectors = the player, white sectors = the pet, pointer at the top.
 * Emits `done` once it has stopped and shown the result.
 */
const props = defineProps<{ first: GameSide; title?: string }>()
const emit = defineEmits<{ done: [] }>()

const SIZE = 96
const SECTORS = 8
const SPIN_MS = 5200
const RESULT_MS = 1300
const TURNS = 7

const canvas = ref<HTMLCanvasElement | null>(null)
const stopped = ref(false)
let raf = 0
let timer = 0

function draw(ctx: CanvasRenderingContext2D, rot: number): void {
  const img = ctx.createImageData(SIZE, SIZE)
  const mid = (SIZE - 1) / 2
  const step = (Math.PI * 2) / SECTORS
  for (let y = 0; y < SIZE; y++) {
    for (let x = 0; x < SIZE; x++) {
      const dx = x - mid
      const dy = y - mid
      const r = Math.hypot(dx, dy)
      if (r > 47) continue
      let v: number
      if (r > 44 || r < 5) v = 128
      else {
        const a = (((Math.atan2(dy, dx) - rot) % (Math.PI * 2)) + Math.PI * 2) % (Math.PI * 2)
        v = Math.floor(a / step) % 2 === 0 ? 16 : 240
      }
      const i = (y * SIZE + x) * 4
      img.data[i] = img.data[i + 1] = img.data[i + 2] = v
      img.data[i + 3] = 255
    }
  }
  ctx.putImageData(img, 0, 0)
}

onMounted(() => {
  const ctx = canvas.value?.getContext('2d')
  if (!ctx) return emit('done')
  // Even sectors are black (the player), odd ones white (the pet). Stop with the pointer (top) well inside one.
  const step = (Math.PI * 2) / SECTORS
  const k = 2 * Math.floor(Math.random() * (SECTORS / 2)) + (props.first === 'player' ? 0 : 1)
  const end = -Math.PI / 2 - (k + 0.2 + Math.random() * 0.6) * step + Math.PI * 2 * TURNS
  const finish = (): void => {
    draw(ctx, end)
    stopped.value = true
    timer = window.setTimeout(() => emit('done'), RESULT_MS)
  }
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return finish()
  const t0 = performance.now()
  const frame = (now: number): void => {
    const t = Math.min(1, (now - t0) / SPIN_MS)
    if (t >= 1) return finish()
    draw(ctx, end * (1 - Math.pow(1 - t, 4)))
    raf = requestAnimationFrame(frame)
  }
  raf = requestAnimationFrame(frame)
})
onBeforeUnmount(() => {
  cancelAnimationFrame(raf)
  clearTimeout(timer)
})
</script>

<template>
  <div class="pixel-wheel">
    <div v-if="title" class="pw-title">{{ title }}</div>
    <div class="pw-stage">
      <div class="pw-pointer" />
      <canvas ref="canvas" :width="SIZE" :height="SIZE" class="pw-canvas" />
    </div>
    <div class="pw-legend">
      <span><i class="pw-sw black" />{{ who('player') }}</span>
      <span><i class="pw-sw white" />{{ who('pet') }}</span>
    </div>
    <div class="pw-result" :class="{ on: stopped }">{{ stopped ? `${who(first)}先手` : '轮盘转动中…' }}</div>
  </div>
</template>

<style scoped>
.pixel-wheel {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 14px;
  padding: 28px 0;
  font-family: 'Cascadia Mono', Consolas, 'Microsoft YaHei UI', monospace;
}
.pw-title {
  font-size: 1.1em;
  letter-spacing: 0.15em;
  opacity: 0.8;
}
.pw-stage {
  position: relative;
  padding: 22px 16px 16px;
  background: #808080;
  border: 4px solid #101010;
  box-shadow: 0 0 0 4px #f0f0f0, 8px 8px 0 4px rgba(0, 0, 0, 0.35);
}
.pw-canvas {
  display: block;
  width: 288px;
  height: 288px;
  image-rendering: pixelated;
}
/* A blocky arrow pointing down at the top of the wheel. */
.pw-pointer {
  position: absolute;
  top: 6px;
  left: 50%;
  z-index: 1;
  width: 12px;
  height: 12px;
  margin-left: -6px;
  background: #f0f0f0;
  box-shadow: 0 0 0 4px #101010, 0 12px 0 -2px #f0f0f0, 0 12px 0 2px #101010;
}
.pw-legend {
  display: flex;
  gap: 24px;
}
.pw-sw {
  display: inline-block;
  width: 14px;
  height: 14px;
  margin-right: 8px;
  vertical-align: -2px;
  border: 2px solid #808080;
}
.pw-sw.black {
  background: #101010;
}
.pw-sw.white {
  background: #f0f0f0;
}
.pw-result {
  min-height: 1.6em;
  font-size: 1.5em;
  font-weight: 700;
  letter-spacing: 0.1em;
  opacity: 0.6;
}
.pw-result.on {
  opacity: 1;
  animation: pw-blink 0.4s steps(2) 2;
}
@keyframes pw-blink {
  50% {
    opacity: 0.2;
  }
}
</style>
