<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import {
  NButton,
  NCard,
  NEmpty,
  NForm,
  NFormItem,
  NSelect,
  NSpace,
  NSpin,
  NSwitch,
  NTabPane,
  NTabs,
  NText
} from 'naive-ui'
import BackendOffline from '../components/BackendOffline.vue'
import FallbackSelects from '../components/FallbackSelects.vue'
import PageHead from '../components/PageHead.vue'
import ProfileEditor from '../components/ProfileEditor.vue'
import SaveBar from '../components/SaveBar.vue'
import TestButton from '../components/TestButton.vue'
import { addProfile, dirty, llm, loadLlm, removeProfile, revertLlm, saveLlm } from '../llm'
import { useTask } from '../task'

const { busy: saving, run } = useTask()
const save = (): Promise<boolean> => run(saveLlm, '已保存')
const activeUid = ref<number | null>(null)

const profileOptions = computed(() => llm.profiles.map((p) => ({ label: p.name || '（未命名）', value: p.uid })))
const active = computed(() => llm.profiles.find((p) => p.uid === activeUid.value) ?? null)

watch(
  () => llm.profiles.map((p) => p.uid),
  (uids) => {
    if (activeUid.value === null || !uids.includes(activeUid.value)) activeUid.value = uids[0] ?? null
  },
  { immediate: true }
)

onMounted(() => {
  if (!llm.loaded) void loadLlm()
})

function onAdd(): void {
  activeUid.value = addProfile().uid
}
</script>

<template>
  <page-head title="AI 模型" />
  <n-spin :show="llm.loading && !llm.loaded">
    <backend-offline v-if="llm.offline || (!llm.loaded && llm.error)" :error="llm.error" :loading="llm.loading" @retry="loadLlm" />
    <n-space v-else-if="llm.loaded" vertical :size="20">
      <n-card title="用途">
        <n-form label-placement="left" label-width="90">
          <n-form-item label="文字聊天" :show-feedback="false">
            <div>
              <n-select v-model:value="llm.roles.chat" :options="profileOptions" clearable placeholder="未指定" style="width: 280px" />
              <fallback-selects role="chat" />
            </div>
          </n-form-item>
          <n-form-item label="记忆" :show-feedback="false" style="margin-top: 12px">
            <n-space vertical :size="4">
              <n-select v-model:value="llm.roles.memory" :options="profileOptions" clearable placeholder="沿用聊天" style="width: 280px" />
              <n-text depth="3" style="font-size: 12px">每聊满 N 轮，用它把对话总结成长期记忆。可以选一个便宜的模型。不指定时沿用聊天的主用和备选。</n-text>
              <fallback-selects role="memory" />
            </n-space>
          </n-form-item>
          <n-form-item label="联网搜索" :show-feedback="false" style="margin-top: 12px">
            <n-space vertical :size="4">
              <n-switch v-model:value="llm.webSearch">
                <template #checked>允许聊天时联网搜索</template>
                <template #unchecked>允许聊天时联网搜索</template>
              </n-switch>
              <n-text depth="3" style="font-size: 12px">总开关。关闭后即使配置里选了搜索方式也不会搜。</n-text>
            </n-space>
          </n-form-item>
        </n-form>
      </n-card>

      <n-card title="配置">
        <template #header-extra>
          <n-button size="small" @click="onAdd">新增配置</n-button>
        </template>
        <n-empty v-if="llm.profiles.length === 0" description="还没有配置，点右上角「新增配置」">
          <template #extra>
            <n-button type="primary" @click="onAdd">新增配置</n-button>
          </template>
        </n-empty>
        <n-tabs
          v-else
          :value="activeUid ?? undefined"
          type="card"
          closable
          size="small"
          @update:value="(uid: number) => (activeUid = uid)"
          @close="(uid: number) => removeProfile(uid)"
        >
          <n-tab-pane v-for="p in llm.profiles" :key="p.uid" :name="p.uid" :tab="p.name || '（未命名）'">
            <profile-editor :profile="p" />
          </n-tab-pane>
        </n-tabs>
        <test-button v-if="active" :profile="active" kind="chat" label="测试对话" />
      </n-card>
    </n-space>
  </n-spin>
  <save-bar v-if="llm.loaded && !llm.offline" :dirty="dirty" :saving="saving" @save="save" @revert="revertLlm" />
</template>
