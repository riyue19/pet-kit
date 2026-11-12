<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { NAlert, NCard, NForm, NFormItem, NSelect, NSpace, NSpin } from 'naive-ui'
import BackendOffline from '../components/BackendOffline.vue'
import FallbackSelects from '../components/FallbackSelects.vue'
import PageHead from '../components/PageHead.vue'
import SaveBar from '../components/SaveBar.vue'
import TestButton from '../components/TestButton.vue'
import { dirty, llm, loadLlm, revertLlm, saveLlm } from '../llm'
import { useTask } from '../task'

const { busy: saving, run } = useTask()
const save = (): Promise<boolean> => run(saveLlm, '已保存')

const profileOptions = computed(() => llm.profiles.map((p) => ({ label: p.name || '（未命名）', value: p.uid })))
const visionProfile = computed(() => llm.profiles.find((p) => p.uid === llm.roles.vision) ?? null)

onMounted(() => {
  if (!llm.loaded) void loadLlm()
})
</script>

<template>
  <page-head title="识图" />
  <n-spin :show="llm.loading && !llm.loaded">
    <backend-offline v-if="llm.offline || (!llm.loaded && llm.error)" :error="llm.error" :loading="llm.loading" @retry="loadLlm" />
    <n-card v-else-if="llm.loaded" title="识图模型">
      <n-space vertical :size="16">
        <n-alert type="info" :show-icon="false">
          屏幕感知（M6）需要看图时，会用这里指定的配置调用视觉模型；只在规则判定需要时才调用。
          所选模型必须支持图像输入（如 gpt-4o、qwen-vl-plus、glm-4v）。配置本身在「AI 模型」页管理。
        </n-alert>
        <n-form label-placement="left" label-width="90">
          <n-form-item label="使用配置" :show-feedback="false">
            <div>
              <n-select v-model:value="llm.roles.vision" :options="profileOptions" clearable placeholder="未指定" style="width: 280px" />
              <fallback-selects role="vision" />
            </div>
          </n-form-item>
        </n-form>
        <test-button :profile="visionProfile" kind="vision" label="测试识图（发送一张红色方块图）" />
      </n-space>
    </n-card>
  </n-spin>
  <save-bar v-if="llm.loaded && !llm.offline" :dirty="dirty" :saving="saving" hint="和「AI 模型」页一起保存" @save="save" @revert="revertLlm" />
</template>
