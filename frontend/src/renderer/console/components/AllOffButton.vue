<script setup lang="ts">
import { ref } from 'vue'
import { NButton, NPopconfirm, useMessage } from 'naive-ui'
import { errorText } from '../store'

/** The one-click "everything off" of the side bar (CLAUDE.md 4.3). Lives inside the message provider. */
defineProps<{ collapsed: boolean }>()
const emit = defineEmits<{ done: [] }>()

const WHAT = '麦克风、屏幕感知、摄像头和主动对话'
const message = useMessage()
const busy = ref(false)

async function allOff(): Promise<void> {
  busy.value = true
  try {
    const res = await window.petConsole.backend('POST', '/api/privacy/off')
    if (res.ok) {
      message.success(`已关闭${WHAT}`)
      emit('done')
    } else if (res.offline) {
      message.error('后端没有在运行，没能关闭。请重新启动桌宠后再试。', { duration: 6000 })
    } else {
      message.error(`没能关闭：${res.error}`, { duration: 6000 })
    }
  } catch (e) {
    message.error(errorText(e), { duration: 6000 })
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <n-popconfirm @positive-click="allOff">
    <template #trigger>
      <n-button size="small" type="error" secondary block :loading="busy" :title="`关闭${WHAT}`">
        <svg v-if="collapsed" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
          <path d="M12 3v9M6.3 6.3a8 8 0 1 0 11.4 0" />
        </svg>
        <template v-else>关闭全部感知</template>
      </n-button>
    </template>
    立即关闭{{ WHAT }}？之后可在「语音」「感知与主动」页重新打开。
  </n-popconfirm>
</template>
