<script setup lang="ts">
import { NAlert, NButton, NCode, NCollapse, NCollapseItem } from 'naive-ui'

defineProps<{ error: string | null; loading: boolean }>()
defineEmits<{ retry: [] }>()
</script>

<template>
  <n-alert type="warning" title="桌宠的后台程序没有在运行">
    <p class="line">这一页的设置要靠它读取和保存。先点「重新连接」试一次；还不行的话，从托盘退出桌宠，再用桌面上的「桌宠」快捷方式重新打开，它会把后台程序一起启动。</p>
    <div class="actions">
      <n-button size="small" :loading="loading" @click="$emit('retry')">重新连接</n-button>
    </div>
    <n-collapse class="detail">
      <n-collapse-item title="详细信息（排查问题时用）" name="detail">
        <p v-if="error" class="line">{{ error }}</p>
        <p class="line">手动启动：在项目根目录打开终端，执行</p>
        <n-code code="cd backend && uv run python -m api" language="bash" />
      </n-collapse-item>
    </n-collapse>
  </n-alert>
</template>

<style scoped>
.line {
  margin: 0 0 6px;
}
.actions {
  margin-top: 10px;
}
.detail {
  margin-top: 12px;
}
</style>
