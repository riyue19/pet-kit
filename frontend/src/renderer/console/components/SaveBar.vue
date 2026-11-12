<script setup lang="ts">
import { NButton, useThemeVars } from 'naive-ui'

/**
 * The one save bar of a settings page: sticks to the bottom of the window while the page scrolls.
 * Put it at the page's root level (a sibling of the n-spin), not inside an n-space or a card:
 * a sticky element cannot leave its parent's box.
 */
withDefaults(
  defineProps<{
    dirty: boolean
    saving?: boolean
    /** Why saving is not possible right now (shown in red; the save button is disabled). */
    problem?: string | null
    /** Shown while there is nothing to save. */
    hint?: string
    revertable?: boolean
  }>(),
  { saving: false, problem: null, hint: '', revertable: true }
)
defineEmits<{ save: []; revert: [] }>()

const theme = useThemeVars()
</script>

<template>
  <div class="save-bar" :style="{ background: theme.bodyColor, borderColor: theme.dividerColor }">
    <span v-if="problem" class="note" :style="{ color: theme.errorColor, opacity: 1 }">不能保存：{{ problem }}</span>
    <span v-else class="note">{{ dirty ? '有未保存的修改' : hint }}</span>
    <div class="buttons">
      <n-button v-if="revertable" :disabled="!dirty" @click="$emit('revert')">撤销修改</n-button>
      <n-button type="primary" :disabled="!dirty || !!problem" :loading="saving" @click="$emit('save')">保存</n-button>
    </div>
  </div>
</template>

<style scoped>
.save-bar {
  position: sticky;
  bottom: 0;
  z-index: 5;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 20px;
  padding: 12px 0;
  border-top: 1px solid;
}
.note {
  min-width: 0;
  font-size: 0.9em;
  opacity: 0.65;
}
.buttons {
  display: flex;
  flex: none;
  gap: 12px;
}
</style>
