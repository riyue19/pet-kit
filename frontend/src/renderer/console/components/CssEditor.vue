<script setup lang="ts">
import { ref } from 'vue'
import { NButton, NInput, NModal } from 'naive-ui'
import { CUSTOM_CSS_MAX_CHARS } from '../../../shared/ui-style'

defineProps<{ placeholder?: string; rows?: number; title?: string }>()
const value = defineModel<string>({ required: true })
const open = ref(false)
</script>

<template>
  <div class="css-editor">
    <n-input
      v-model:value="value"
      type="textarea"
      :rows="rows ?? 5"
      :maxlength="CUSTOM_CSS_MAX_CHARS"
      :placeholder="placeholder"
      class="css-input"
    />
    <n-button size="tiny" quaternary class="expand" @click="open = true">放大编辑</n-button>
  </div>
  <n-modal v-model:show="open" preset="card" :title="title ?? '自定义 CSS'" style="width: min(1000px, 92vw)" :auto-focus="true">
    <n-input
      v-model:value="value"
      type="textarea"
      :rows="26"
      :maxlength="CUSTOM_CSS_MAX_CHARS"
      show-count
      :placeholder="placeholder"
      class="css-input big"
    />
    <template #action>
      <n-button type="primary" @click="open = false">完成</n-button>
      <span class="note">改动已实时应用到草稿，记得回到页面点「保存」。</span>
    </template>
  </n-modal>
</template>

<style scoped>
.css-editor {
  position: relative;
  width: 100%;
}
.expand {
  position: absolute;
  right: 6px;
  bottom: 4px;
  z-index: 1;
}
.css-input :deep(textarea) {
  font-family: Consolas, 'Cascadia Mono', monospace;
  font-size: 12.5px;
  tab-size: 2;
}
.note {
  margin-left: 12px;
  font-size: 0.9em;
  opacity: 0.7;
}
</style>
