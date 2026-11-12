<script setup lang="ts">
import { ref } from 'vue'
import { NAlert, NButton, NSpace } from 'naive-ui'
import type { LlmTestResult } from '../../../shared/backend'
import { testProfile, type DraftProfile } from '../llm'
import { errorText } from '../store'

const props = defineProps<{ profile: DraftProfile | null; kind: 'chat' | 'vision'; label: string }>()

const running = ref(false)
const result = ref<LlmTestResult | null>(null)
const failure = ref<string | null>(null)

async function run(): Promise<void> {
  if (!props.profile) return
  running.value = true
  result.value = null
  failure.value = null
  try {
    result.value = await testProfile(props.profile, props.kind)
  } catch (e) {
    failure.value = errorText(e)
  } finally {
    running.value = false
  }
}
</script>

<template>
  <n-space vertical :size="10">
    <n-space align="center">
      <n-button :disabled="!profile" :loading="running" secondary type="primary" @click="run">{{ label }}</n-button>
      <span v-if="profile?.newKey.trim()" class="hint">测试使用已保存的 Key；新输入的 Key 需先保存</span>
    </n-space>
    <n-alert v-if="result?.ok" type="success" :title="`成功 · ${result.latency_ms} ms · ${result.model ?? ''}`">
      {{ result.reply || '（空回复）' }}
    </n-alert>
    <n-alert v-else-if="result" type="error" title="失败">{{ result.error }}</n-alert>
    <n-alert v-else-if="failure" type="error" title="无法测试">{{ failure }}</n-alert>
  </n-space>
</template>

<style scoped>
.hint {
  font-size: 0.85em;
  opacity: 0.65;
}
</style>
