<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { NButton, NSlider, NTag, useMessage } from 'naive-ui'
import type { FishingCommand } from '../../../shared/backend'
import { fishing, fishingCommand, saveFishingSettings, waitText } from '../fishing'
import { refreshScene, scene, sceneElement, startScene, stopScene } from '../fishing-scene'
import { petName } from '../games'
import { errorText } from '../store'

defineEmits<{ manage: [] }>()

const message = useMessage()
const stage = ref<HTMLDivElement | null>(null)
const s = computed(() => fishing.state)

// ---------- the picture ----------
const nowMs = ref(Date.now())
let clock: number | undefined
onMounted(() => {
  stage.value?.appendChild(sceneElement())
  startScene()
  clock = window.setInterval(() => (nowMs.value = Date.now()), 1000)
})
onBeforeUnmount(() => {
  stopScene()
  window.clearInterval(clock)
})

const statusText = computed(() => {
  if (!scene.loaded) return '…'
  if (!scene.enabled) return '竿收着。抛竿后要等上一阵才会有动静。'
  if (scene.biting) return '有动静！点画面收线'
  if (scene.castTs === null) return '正在收线…'
  return `线在水里，已等待 ${waitText(nowMs.value / 1000 - scene.skew - scene.castTs)}`
})
const rangeText = computed(() => {
  const st = s.value?.settings
  return st ? `每一竿要等 ${waitText(st.wait_min_sec)} 到 ${waitText(st.wait_max_sec)} 不等，什么时候咬钩事先看不出来。` : ''
})

// ---------- buttons ----------
async function run(cmd: FishingCommand, args: string[] = []): Promise<void> {
  if (fishing.busy) return
  try {
    const res = await fishingCommand(cmd, args, false)
    if (res.ok) message.success(res.text)
    else message.warning(res.text)
    refreshScene()
  } catch (e) {
    message.error(errorText(e))
  }
}

async function place(placed: boolean): Promise<void> {
  if (fishing.busy) return
  try {
    await saveFishingSettings({ hud_placed: placed })
    scene.placed = placed
  } catch (e) {
    message.error(errorText(e))
  }
}

// The size slider saves a moment after the last change.
const size = ref(1)
let sizeTimer: number | undefined
watch(
  () => scene.scale,
  (v) => {
    if (sizeTimer === undefined) size.value = v
  },
  { immediate: true }
)
function onSize(v: number): void {
  size.value = v
  window.clearTimeout(sizeTimer)
  sizeTimer = window.setTimeout(async () => {
    sizeTimer = undefined
    try {
      await saveFishingSettings({ hud_scale: v })
      scene.scale = v
    } catch (e) {
      message.error(errorText(e))
    }
  }, 400)
}
</script>

<template>
  <div class="scene">
    <div class="top">
      <span class="top-label">桌宠旁的小窗</span>
      <n-button size="small" :type="scene.placed ? 'primary' : 'default'" :disabled="fishing.busy" @click="place(true)">放置</n-button>
      <n-button size="small" :type="scene.placed ? 'default' : 'primary'" :disabled="fishing.busy" @click="place(false)">收回</n-button>
      <span class="top-label size-label">大小</span>
      <n-slider :value="size" :min="0.6" :max="2.5" :step="0.1" :disabled="!scene.placed" :format-tooltip="(v: number) => `${Math.round(v * 100)}%`" class="size" @update:value="onSize" />
      <n-button size="small" quaternary class="manage" @click="$emit('manage')">背包 · 图鉴 · 设置</n-button>
    </div>

    <div ref="stage" class="stage" :class="{ biting: scene.biting }"></div>

    <p class="status num">{{ statusText }}</p>
    <p v-if="scene.error" class="hint error">{{ scene.error }}</p>

    <div class="buttons">
      <n-button type="primary" :disabled="fishing.busy || scene.enabled" @click="run('start')">抛竿</n-button>
      <n-button :disabled="fishing.busy || !scene.enabled" @click="run('stop')">收竿</n-button>
    </div>

    <div v-if="s" class="baits">
      <span class="top-label">鱼饵</span>
      <n-button
        v-for="b in s.baits"
        :key="b.id"
        size="small"
        :type="b.id === s.bait ? 'primary' : 'default'"
        :disabled="fishing.busy || b.id === s.bait || (b.qty !== null && b.qty <= 0)"
        @click="run('bait', [b.id])"
      >
        {{ b.name }}<template v-if="b.qty !== null"> ×{{ b.qty }}</template>
      </n-button>
      <n-tag v-if="s" size="small" round :bordered="false" class="coins num">金币 {{ s.coins }}</n-tag>
    </div>

    <p class="hint">
      {{ rangeText }}收竿会把线空着收回来，这一竿等过的时间不算数。稀有鱼咬钩时画面会出现“!”，点一下必中；不点的话{{ petName }}自己试着收线。换饵对还在水里的这一竿也生效，没有的饵去「背包 · 图鉴 · 设置」里买。
    </p>
  </div>
</template>

<style src="../../fishing-hud.css"></style>

<style scoped>
.scene {
  display: flex;
  flex-direction: column;
  gap: 14px;
  max-width: 680px;
}
.num {
  font-variant-numeric: tabular-nums;
}
.top,
.baits,
.buttons {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
}
.top-label {
  font-size: 0.9em;
  opacity: 0.7;
}
.size-label {
  margin-left: 8px;
}
.size {
  width: 140px;
}
.manage {
  margin-left: auto;
}
.stage {
  width: 100%;
}
.stage.biting {
  cursor: pointer;
}
.status {
  margin: 0;
  font-size: 1.1em;
  font-weight: 600;
  text-align: center;
}
.buttons {
  justify-content: center;
}
.buttons :deep(.n-button) {
  min-width: 110px;
}
.coins {
  margin-left: auto;
}
.hint {
  margin: 0;
  font-size: 0.9em;
  line-height: 1.7;
  opacity: 0.65;
}
.hint.error {
  color: #d03050;
  opacity: 1;
}
</style>
