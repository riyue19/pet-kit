<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'

/**
 * One status value as a progress bar. Class names are stable so the console's custom CSS can restyle it
 * (see the hint on the 界面 page): .status-bar, .sb-head, .sb-name, .sb-tier, .sb-value, .sb-track, .sb-fill, .sb-tick, .sb-zero.
 * While a change is being shown: .sb-changed plus .sb-up / .sb-down on the bar, and a .sb-delta ("+0.3") next to the number.
 */
const props = defineProps<{
  id: string
  name: string
  value: number
  min: number
  max: number
  color: string
  tier?: string | null
  /** Lower bounds of the tiers, drawn as ticks on the track. */
  ticks?: number[]
  note?: string
}>()

const span = computed(() => Math.max(1e-9, props.max - props.min))
const pct = (v: number): number => Math.min(100, Math.max(0, ((v - props.min) / span.value) * 100))
const fill = computed(() => pct(props.value))
// A range that goes below zero gets a marker at 0, so "negative" is visible at a glance.
const zero = computed(() => (props.min < 0 && props.max > 0 ? pct(0) : null))
const marks = computed(() => (props.ticks ?? []).filter((t) => t > props.min && t < props.max).map(pct))
const num = (v: number): string => String(+v.toFixed(2))

// Each bar animates its own changes: the difference floats up next to the number and the track pulses.
const CHANGE_MS = 1800
const delta = ref<number | null>(null)
const tick = ref(0)
let timer: number | null = null
watch(
  () => props.value,
  (v, old) => {
    const d = +(v - old).toFixed(2)
    if (!d) return
    delta.value = d
    tick.value++
    if (timer !== null) clearTimeout(timer)
    timer = window.setTimeout(() => (delta.value = null), CHANGE_MS)
  }
)
onBeforeUnmount(() => {
  if (timer !== null) clearTimeout(timer)
})
</script>

<template>
  <div class="status-bar" :data-stat="id" :class="{ negative: value < 0, 'sb-changed': delta !== null, 'sb-up': (delta ?? 0) > 0, 'sb-down': (delta ?? 0) < 0 }" :style="{ '--sb-color': color }">
    <div class="sb-head">
      <span class="sb-name">{{ name }}</span>
      <span v-if="tier" class="sb-tier">{{ tier }}</span>
      <span class="sb-value">
        <span v-if="delta !== null" :key="tick" class="sb-delta">{{ delta > 0 ? '+' : '' }}{{ num(delta) }}</span>
        <b :key="`v${tick}`">{{ num(value) }}</b> / {{ num(max) }}
      </span>
    </div>
    <div class="sb-track">
      <div class="sb-fill" :style="{ width: fill + '%' }" />
      <i v-for="(m, i) in marks" :key="i" class="sb-tick" :style="{ left: m + '%' }" />
      <i v-if="zero !== null" class="sb-zero" :style="{ left: zero + '%' }" />
    </div>
    <div v-if="note" class="sb-note">{{ note }}</div>
  </div>
</template>

<!-- Not scoped, single-class selectors: custom CSS such as `.status-card .status-bar { --sb-height: 20px }` wins. -->
<style>
.status-bar {
  --sb-color: #ff7eb6;
  --sb-height: 14px;
  --sb-radius: 999px;
  --sb-track: color-mix(in srgb, var(--sb-color) 16%, transparent);
  --sb-fill: linear-gradient(90deg, color-mix(in srgb, var(--sb-color) 55%, white), var(--sb-color));
  --sb-glow: 0 0 10px color-mix(in srgb, var(--sb-color) 55%, transparent);
}
/* The built-in 好感度 bar is drawn a little larger. */
.sb-main {
  --sb-height: 20px;
  font-size: 1.08em;
}
.sb-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 6px;
}
.sb-name {
  font-weight: 600;
}
.sb-tier {
  padding: 0 8px;
  font-size: 0.85em;
  line-height: 1.7;
  color: var(--sb-color);
  background: color-mix(in srgb, var(--sb-color) 14%, transparent);
  border-radius: 999px;
}
.sb-value {
  margin-left: auto;
  font-size: 0.9em;
  opacity: 0.75;
  font-variant-numeric: tabular-nums;
}
.sb-value b {
  font-size: 1.25em;
  color: var(--sb-color);
}
.sb-track {
  position: relative;
  height: var(--sb-height);
  border-radius: var(--sb-radius);
  background: var(--sb-track);
  overflow: hidden;
}
.sb-fill {
  position: relative;
  height: 100%;
  min-width: 0;
  border-radius: var(--sb-radius);
  background: var(--sb-fill);
  box-shadow: var(--sb-glow);
  transition: width 0.6s cubic-bezier(0.2, 0.8, 0.3, 1);
  overflow: hidden;
}
/* Soft highlight sweeping across the filled part. */
.sb-fill::after {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(100deg, transparent 30%, rgba(255, 255, 255, 0.45) 50%, transparent 70%);
  transform: translateX(-100%);
  animation: sb-shine 3.2s ease-in-out infinite;
}
@keyframes sb-shine {
  0%,
  55% {
    transform: translateX(-100%);
  }
  100% {
    transform: translateX(100%);
  }
}
.sb-tick,
.sb-zero {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 1px;
  background: rgba(255, 255, 255, 0.55);
  mix-blend-mode: overlay;
  pointer-events: none;
}
.sb-zero {
  width: 2px;
  background: color-mix(in srgb, var(--sb-color) 80%, black);
  mix-blend-mode: normal;
  opacity: 0.55;
}
.sb-note {
  margin-top: 5px;
  font-size: 0.85em;
  opacity: 0.6;
}
/* A value just changed: the number pops, the difference floats up, the track glows once. */
.sb-value {
  position: relative;
}
.sb-delta {
  position: absolute;
  right: 0;
  bottom: 100%;
  font-weight: 700;
  color: var(--sb-color);
  white-space: nowrap;
  pointer-events: none;
  animation: sb-delta 1.8s cubic-bezier(0.2, 0.8, 0.3, 1) both;
}
.sb-down .sb-delta {
  color: #7d8597;
}
.sb-changed .sb-value b {
  display: inline-block;
  animation: sb-pop 0.6s cubic-bezier(0.2, 0.8, 0.3, 1);
}
.sb-changed .sb-track {
  animation: sb-pulse 1.2s ease-out;
}
@keyframes sb-delta {
  0% {
    opacity: 0;
    transform: translateY(8px) scale(0.6);
  }
  15% {
    opacity: 1;
    transform: translateY(0) scale(1.15);
  }
  25% {
    transform: translateY(0) scale(1);
  }
  80% {
    opacity: 1;
    transform: translateY(-6px);
  }
  100% {
    opacity: 0;
    transform: translateY(-16px);
  }
}
@keyframes sb-pop {
  40% {
    transform: scale(1.35);
  }
}
@keyframes sb-pulse {
  0% {
    box-shadow: 0 0 0 0 color-mix(in srgb, var(--sb-color) 60%, transparent);
  }
  100% {
    box-shadow: 0 0 0 8px transparent;
  }
}
@media (prefers-reduced-motion: reduce) {
  .sb-delta,
  .sb-changed .sb-value b,
  .sb-changed .sb-track {
    animation: none;
  }
}
@media (prefers-reduced-motion: reduce) {
  .sb-fill::after {
    animation: none;
  }
}
</style>
