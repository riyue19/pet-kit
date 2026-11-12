<script setup lang="ts">
import { onMounted } from 'vue'
import { NCard, NForm, NFormItem, NInput, NInputNumber, NSpace, NSpin } from 'naive-ui'
import BackendOffline from '../components/BackendOffline.vue'
import PageHead from '../components/PageHead.vue'
import SaveBar from '../components/SaveBar.vue'
import { useTask } from '../task'
import { dirty, loadPersona, nameError, persona, promptError, revertPersona, savePersona, valid } from '../persona'

const { busy: saving, run } = useTask()
const save = (): Promise<boolean> => run(savePersona, '已保存')

onMounted(() => {
  // Re-read unless there are unsaved edits, so hand edits to persona.yaml aren't silently overwritten.
  if (!persona.loaded || !dirty.value) void loadPersona()
})
</script>

<template>
  <page-head title="人设" />
  <n-spin :show="persona.loading && !persona.loaded">
    <backend-offline v-if="persona.offline || (!persona.loaded && persona.error)" :error="persona.error" :loading="persona.loading" @retry="loadPersona" />
    <n-space v-else-if="persona.loaded" vertical :size="20">
      <span class="hint">保存后下一句对话立即生效，无需重启。</span>
      <n-card>
        <n-form label-placement="top">
          <n-form-item label="名字" :validation-status="nameError ? 'error' : undefined" :feedback="nameError ?? undefined">
            <n-input v-model:value="persona.form.name" :maxlength="32" show-count placeholder="桌宠的名字" style="max-width: 320px" />
          </n-form-item>
          <n-form-item label="人设提示词" :validation-status="promptError ? 'error' : undefined" :feedback="promptError ?? '只写性格和说话方式。输出格式（JSON、可用的表情/动作标签）由后端自动追加，不用写。'">
            <n-input v-model:value="persona.form.system_prompt" type="textarea" :maxlength="8000" show-count :autosize="{ minRows: 8, maxRows: 24 }" />
          </n-form-item>
          <n-form-item label="历史轮数" feedback="每次请求带上最近几轮对话（一问一答算一轮）。0 表示不带历史。越大越贵。">
            <n-input-number v-model:value="persona.form.history_turns" :min="0" :max="50" :step="1" :precision="0" style="width: 140px" />
          </n-form-item>
          <n-form-item label="回复字数上限" feedback="提示模型单次回复的最大字数（气泡放得下的长度）。">
            <n-input-number v-model:value="persona.form.max_reply_chars" :min="10" :max="500" :step="10" :precision="0" style="width: 140px" />
          </n-form-item>
        </n-form>
      </n-card>
    </n-space>
  </n-spin>
  <save-bar
    v-if="persona.loaded && !persona.offline"
    :dirty="dirty"
    :saving="saving"
    :problem="nameError ?? promptError ?? (valid ? null : '有数字没有填')"
    @save="save"
    @revert="revertPersona"
  />
</template>
