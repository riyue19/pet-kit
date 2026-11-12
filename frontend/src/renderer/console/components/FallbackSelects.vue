<script setup lang="ts">
import { computed } from 'vue'
import { NSelect, NText } from 'naive-ui'
import { llm, type LlmRole } from '../llm'

const props = defineProps<{ role: LlmRole }>()

const LABELS = ['第二顺位', '第三顺位']

// A profile can appear once per chain: hide the primary and whatever the other slot already uses.
function options(slot: number): { label: string; value: number }[] {
  const taken = new Set<number | null>([llm.roles[props.role], llm.fallbacks[props.role][1 - slot]])
  return llm.profiles.filter((p) => !taken.has(p.uid)).map((p) => ({ label: p.name || '（未命名）', value: p.uid }))
}
const enabled = computed(() => llm.roles[props.role] !== null)
</script>

<template>
  <div v-if="enabled" class="fallbacks">
    <div v-for="(label, slot) in LABELS" :key="slot" class="row">
      <n-text depth="3" class="label">{{ label }}</n-text>
      <n-select
        v-model:value="llm.fallbacks[role][slot]"
        :options="options(slot)"
        :disabled="slot === 1 && llm.fallbacks[role][0] === null"
        clearable
        placeholder="不设置"
        size="small"
        style="width: 280px"
      />
    </div>
    <n-text depth="3" style="font-size: 12px">主用报错或超时时，依次改用这两个；失败的配置 1 分钟内排到最后。</n-text>
  </div>
</template>

<style scoped>
.fallbacks {
  margin-top: 8px;
}
.row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.label {
  width: 60px;
  font-size: 12px;
}
</style>
