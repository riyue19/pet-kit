<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { NAlert, NButton, NCard, NInputNumber, NSpace, NSwitch, NTag, useMessage } from 'naive-ui'
import { errorText } from '../store'
import { cameraLook, cameraState as c, formatGreetingTime, loadCamera, refreshCamera } from '../camera'

const message = useMessage()
const looking = ref(false)

const canLook = computed(() => c.snapshot !== '' && JSON.parse(c.snapshot).enabled === true && c.runtime.open)

let timer: ReturnType<typeof setInterval> | null = null

function tick(): void {
  if (document.visibilityState === 'hidden') return
  void refreshCamera().catch(() => undefined)
}

function onVisible(): void {
  if (document.visibilityState === 'visible') tick()
}

onMounted(() => {
  if (!c.loaded) void loadCamera().catch(() => undefined)
  else tick()
  timer = setInterval(tick, 3000)
  document.addEventListener('visibilitychange', onVisible)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
  document.removeEventListener('visibilitychange', onVisible)
})

async function onLook(): Promise<void> {
  looking.value = true
  try {
    const r = await cameraLook()
    if (r.scheduled) message.info('好的，它会看一眼')
    else message.error(r.error ?? '无法安排', { duration: 6000 })
  } catch (e) {
    message.error(errorText(e), { duration: 6000 })
  } finally {
    looking.value = false
  }
}
</script>

<template>
  <n-card title="摄像头（可选）">
    <n-space vertical :size="14">
      <span class="hint">摄像头默认关闭。开启后画面只在本机用来判断人在不在，不保存、不上传；只有在你离开一阵后回来时，才会抽一帧发给识图模型打个招呼。开启期间桌宠左上角会显示红色「摄像头开启中」。</span>

      <n-space v-if="c.offline" align="center">
        <span class="error">后端未连接：{{ c.error }}</span>
        <n-button size="small" @click="loadCamera">重试</n-button>
      </n-space>

      <template v-else-if="c.loaded">
        <n-space align="center">
          <n-switch v-model:value="c.form.enabled" />
          <span>启用摄像头</span>
        </n-space>
        <n-space align="center">
          <span>设备号</span>
          <n-input-number v-model:value="c.form.device_index" :min="0" :max="9" :step="1" :precision="0" style="width: 130px" />
        </n-space>
        <n-space align="center">
          <span>检测间隔（秒）</span>
          <n-input-number v-model:value="c.form.check_seconds" :min="1" :max="30" :step="1" style="width: 130px" />
        </n-space>
        <n-space align="center">
          <span>离开判定（秒）</span>
          <n-input-number v-model:value="c.form.away_after_seconds" :min="5" :max="600" :step="5" :precision="0" style="width: 130px" />
          <span class="hint">多久没检测到人脸算离开</span>
        </n-space>
        <n-space align="center">
          <n-switch v-model:value="c.form.greet_on_return" />
          <span>回来时打招呼</span>
          <span class="hint">需要同时开启「主动对话」总开关和识图 LLM</span>
        </n-space>
        <n-space align="center">
          <span>离开多少分钟以上才打招呼</span>
          <n-input-number v-model:value="c.form.greet_min_away_minutes" :min="1" :max="240" :step="1" :precision="0" :disabled="!c.form.greet_on_return" style="width: 130px" />
        </n-space>

        <span class="hint">摄像头的设置和本页其他设置一起，用页面底部的「保存」保存。</span>

        <n-space vertical :size="10">
          <n-space align="center" :size="8">
            <span class="label">摄像头</span>
            <n-tag size="small" :type="c.runtime.open ? 'success' : 'default'">{{ c.runtime.open ? '已打开' : '未打开' }}</n-tag>
            <span class="label">人</span>
            <n-tag size="small" :type="c.runtime.present === true ? 'success' : 'default'">
              {{ c.runtime.present === null ? '未检测' : c.runtime.present ? '在' : '不在' }}
            </n-tag>
          </n-space>
          <n-alert v-if="c.runtime.error" type="warning" :closable="false">{{ c.runtime.error }}</n-alert>
          <n-alert v-if="!c.runtime.model_ready" type="warning" :closable="false">
            缺少人脸检测模型，请在 backend/ 运行 uv run python scripts/fetch_models.py
          </n-alert>
          <n-alert v-if="c.runtime.vision_profile === null" type="warning" :closable="false">
            还没指定识图 LLM，打招呼不会生效（在「识图」页设置）
          </n-alert>
          <n-space align="center" :size="8">
            <span class="label">上次打招呼</span>
            <span v-if="c.runtime.last_greeting_at === null" class="hint">还没有</span>
            <span v-else>{{ formatGreetingTime(c.runtime.last_greeting_at) }} · {{ c.runtime.last_greeting_text ?? '' }}</span>
          </n-space>
          <n-space align="center">
            <n-button :loading="looking" :disabled="!canLook" @click="onLook">现在让它看一眼</n-button>
            <span class="hint">测试用，模拟「刚回到电脑前」。需要已启用且摄像头已打开。</span>
          </n-space>
        </n-space>
      </template>
    </n-space>
  </n-card>
</template>

<style scoped>
.label {
  min-width: 5em;
  opacity: 0.8;
}
</style>
