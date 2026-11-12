<script setup lang="ts">
import { computed, ref } from 'vue'
import {
  NButton,
  NForm,
  NFormItemGi,
  NGrid,
  NInput,
  NInputNumber,
  NPopconfirm,
  NSelect,
  NSpace,
  NTag,
  NText,
  useMessage
} from 'naive-ui'
import { clearKey, parseExtra, type DraftProfile } from '../llm'
import { errorText } from '../store'

const props = defineProps<{ profile: DraftProfile }>()
const message = useMessage()
const clearing = ref(false)

const extraCheck = computed(() => parseExtra(props.profile.extraText))

async function onClearKey(): Promise<void> {
  clearing.value = true
  try {
    await clearKey(props.profile)
    message.success('已清除 API Key')
  } catch (e) {
    message.error(errorText(e))
  } finally {
    clearing.value = false
  }
}

const SEARCH_OPTIONS = [
  { label: '关闭', value: 'off' },
  { label: 'googleSearch 函数（多数中转站接 Gemini 用这个）', value: 'google_function' },
  { label: 'web_search_options（OpenAI 搜索模型 / 部分中转站）', value: 'openai_options' }
]

// NSelect values can't be booleans, so map through strings.
const STREAM_OPTIONS = [
  { label: '非流式（一次返回）', value: 'off' },
  { label: '流式（边生成边返回）', value: 'on' }
]
const streamMode = computed<string>({
  get: () => (props.profile.stream ? 'on' : 'off'),
  set: (v) => {
    props.profile.stream = v === 'on'
  }
})

const OPTIONAL = '不发送（用服务端默认）'
</script>

<template>
  <n-form label-placement="top" :show-require-mark="false">
    <n-grid :cols="2" :x-gap="20">
      <n-form-item-gi label="名称">
        <n-input v-model:value="profile.name" maxlength="32" placeholder="如：DeepSeek" />
      </n-form-item-gi>
      <n-form-item-gi label="模型">
        <n-input v-model:value="profile.model" placeholder="如：gpt-4o-mini / deepseek-chat / qwen-vl-plus" />
      </n-form-item-gi>
      <n-form-item-gi :span="2" label="接口地址（OpenAI 兼容）">
        <n-input v-model:value="profile.base_url" placeholder="https://api.openai.com/v1" />
      </n-form-item-gi>
      <n-form-item-gi :span="2" label="API Key">
        <n-space vertical style="width: 100%" :size="6">
          <n-space align="center" :size="8">
            <n-tag v-if="profile.key.set" type="success" size="small" :bordered="false">
              已设置{{ profile.key.hint ? `（${profile.key.hint}）` : '' }}
            </n-tag>
            <n-tag v-else size="small" :bordered="false">未设置</n-tag>
            <n-popconfirm v-if="profile.key.set" @positive-click="onClearKey">
              <template #trigger>
                <n-button size="tiny" quaternary type="error" :loading="clearing">清除</n-button>
              </template>
              确定从系统凭据库删除这个 Key？
            </n-popconfirm>
          </n-space>
          <n-input
            v-model:value="profile.newKey"
            type="password"
            show-password-on="click"
            :input-props="{ autocomplete: 'off', spellcheck: false }"
            :placeholder="profile.key.set ? '输入新 Key 以替换（留空则不变）' : '输入 API Key（本地模型可留空）'"
          />
          <n-text depth="3" class="note">Key 只保存在 Windows 凭据管理器，不写入配置文件，也不会再显示出来。</n-text>
        </n-space>
      </n-form-item-gi>

      <n-form-item-gi label="max_tokens">
        <n-input-number v-model:value="profile.max_tokens" :min="1" :max="1000000" :step="256" clearable :placeholder="OPTIONAL" style="width: 100%" />
      </n-form-item-gi>
      <n-form-item-gi label="temperature（0 ~ 2）">
        <n-input-number v-model:value="profile.temperature" :min="0" :max="2" :step="0.1" :precision="2" clearable :placeholder="OPTIONAL" style="width: 100%" />
      </n-form-item-gi>
      <n-form-item-gi label="top_p（0 ~ 1）">
        <n-input-number v-model:value="profile.top_p" :min="0" :max="1" :step="0.05" :precision="2" clearable :placeholder="OPTIONAL" style="width: 100%" />
      </n-form-item-gi>
      <n-form-item-gi label="超时（秒）">
        <n-input-number v-model:value="profile.timeout" :min="1" :max="600" :step="5" style="width: 100%" />
      </n-form-item-gi>
      <n-form-item-gi label="presence_penalty（-2 ~ 2）">
        <n-input-number v-model:value="profile.presence_penalty" :min="-2" :max="2" :step="0.1" :precision="2" clearable :placeholder="OPTIONAL" style="width: 100%" />
      </n-form-item-gi>
      <n-form-item-gi label="frequency_penalty（-2 ~ 2）">
        <n-input-number v-model:value="profile.frequency_penalty" :min="-2" :max="2" :step="0.1" :precision="2" clearable :placeholder="OPTIONAL" style="width: 100%" />
      </n-form-item-gi>

      <n-form-item-gi :span="2" label="输出方式">
        <n-space vertical :size="6" style="width: 100%">
          <n-select v-model:value="streamMode" :options="STREAM_OPTIONS" style="width: 100%" />
          <n-text depth="3" class="note">两种方式桌宠收到的内容一样（都是等整句生成完再显示）。有些中转站只支持流式，或非流式容易超时，这时选流式。</n-text>
        </n-space>
      </n-form-item-gi>

      <n-form-item-gi :span="2" label="联网搜索">
        <n-space vertical :size="6" style="width: 100%">
          <n-select v-model:value="profile.web_search" :options="SEARCH_OPTIONS" style="width: 100%" />
          <n-text depth="3" class="note">让模型自己联网搜索最新信息。用哪种写法取决于中转站，可以选好后点「测试」，问它今天的新闻试试。开启后每次聊天都会附带搜索工具，模型自己决定要不要搜；问题明显需要最新信息时（新闻、价格、比分、上映等）还会额外提示它先搜。看屏幕吐槽时不会搜索。</n-text>
        </n-space>
      </n-form-item-gi>

      <n-form-item-gi
        :span="2"
        label="自定义参数（JSON 对象，原样并入请求体）"
        :validation-status="extraCheck.ok ? undefined : 'error'"
        :feedback="extraCheck.ok ? '例如 {&quot;seed&quot;: 42, &quot;response_format&quot;: {&quot;type&quot;: &quot;json_object&quot;}}' : extraCheck.error"
      >
        <n-input
          v-model:value="profile.extraText"
          type="textarea"
          :autosize="{ minRows: 3, maxRows: 12 }"
          placeholder="{}"
          class="mono"
          :input-props="{ spellcheck: false }"
        />
      </n-form-item-gi>
    </n-grid>
  </n-form>
</template>

<style scoped>
.note {
  font-size: 0.85em;
}
.mono :deep(textarea) {
  font-family: Consolas, 'Cascadia Mono', monospace;
}
</style>
