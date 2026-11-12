<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'

/** Whether the backend answers, shown once in the side bar (instead of a tag on every page). */
defineProps<{ collapsed: boolean }>()

const POLL_MS = 5000
// null = not checked yet.
const online = ref<boolean | null>(null)
let timer: ReturnType<typeof setInterval> | null = null

async function check(): Promise<void> {
  if (document.visibilityState === 'hidden') return
  try {
    online.value = (await window.petConsole.backend('GET', '/api/health')).ok
  } catch {
    online.value = false
  }
}

onMounted(() => {
  void check()
  timer = setInterval(() => void check(), POLL_MS)
  document.addEventListener('visibilitychange', check)
})
onBeforeUnmount(() => {
  if (timer !== null) clearInterval(timer)
  document.removeEventListener('visibilitychange', check)
})
</script>

<template>
  <div v-if="online !== null" class="backend-status" :class="{ off: !online, collapsed }" :title="online ? '桌宠的后台程序正在运行' : '桌宠的后台程序没有在运行，设置暂时读不到也存不了'">
    <span class="dot" />
    <span v-if="!collapsed">{{ online ? '后台运行中' : '后台未运行' }}</span>
  </div>
</template>

<style scoped>
.backend-status {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 26px 10px;
  font-size: 0.85em;
  opacity: 0.75;
  white-space: nowrap;
}
.backend-status.collapsed {
  justify-content: center;
  padding: 0 0 10px;
}
.dot {
  width: 8px;
  height: 8px;
  flex: none;
  border-radius: 50%;
  background: #18a058;
}
.off .dot {
  background: #d03050;
}
.off {
  opacity: 1;
}
</style>
