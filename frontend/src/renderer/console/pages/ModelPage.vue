<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import {
  NAlert,
  NButton,
  NCard,
  NForm,
  NFormItem,
  NInputNumber,
  NRadioButton,
  NRadioGroup,
  NSelect,
  NSlider,
  NSpace,
  NSpin,
  useMessage
} from 'naive-ui'
import type { ModelEntry, ModelLayout, ModelSettings } from '../../../shared/ipc'
import { errorText } from '../store'
import ActionsCard from '../components/ActionsCard.vue'
import PageHead from '../components/PageHead.vue'

const DEFAULT_CUSTOM_SCALE = 0.15

const message = useMessage()
const loading = ref(true)
const saving = ref(false)
const importing = ref(false)
const models = ref<ModelEntry[]>([])
const saved = ref<ModelSettings | null>(null)

const path = ref('')
const scaleMode = ref<'auto' | 'custom'>('auto')
const customScale = ref(DEFAULT_CUSTOM_SCALE)
const framing = ref<'full' | 'half'>('full')
const halfRatio = ref(0.55)

const options = computed(() => {
  const list = models.value.map((m) => ({ label: m.name, value: m.path }))
  if (path.value && !list.some((o) => o.value === path.value)) {
    list.unshift({ label: `${path.value}（不在模型库中）`, value: path.value })
  }
  return list
})

const layout = computed<ModelLayout>(() => ({
  scale: scaleMode.value === 'auto' ? 'auto' : customScale.value,
  framing: framing.value,
  halfRatio: halfRatio.value
}))

const dirty = computed(() => {
  const s = saved.value
  if (!s) return false
  const l = layout.value
  return s.path !== path.value || s.scale !== l.scale || s.framing !== l.framing || s.halfRatio !== l.halfRatio
})

function apply(s: ModelSettings): void {
  path.value = s.path
  scaleMode.value = s.scale === 'auto' ? 'auto' : 'custom'
  if (s.scale !== 'auto') customScale.value = s.scale
  framing.value = s.framing
  halfRatio.value = s.halfRatio
}

onMounted(async () => {
  try {
    const state = await window.petConsole.modelState()
    models.value = state.models
    saved.value = { ...state.current }
    apply(state.current)
  } catch (e) {
    message.error(errorText(e))
  } finally {
    loading.value = false
  }
})

watch(
  layout,
  (l) => {
    if (!loading.value && customScaleValid.value) window.petConsole.previewModel({ ...l })
  },
  { deep: true }
)

const customScaleValid = computed(() => scaleMode.value === 'auto' || customScale.value > 0)

async function importNew(): Promise<void> {
  importing.value = true
  try {
    const entry = await window.petConsole.importModel()
    if (!entry) return
    models.value = (await window.petConsole.modelState()).models
    path.value = entry.path
    message.success(`已导入：${entry.name}`)
  } catch (e) {
    message.error(errorText(e))
  } finally {
    importing.value = false
  }
}

async function save(): Promise<void> {
  if (!path.value) {
    message.warning('请先选择模型')
    return
  }
  saving.value = true
  try {
    const next: ModelSettings = { path: path.value, ...layout.value }
    await window.petConsole.saveModel(next)
    saved.value = next
    message.success('已保存')
  } catch (e) {
    message.error(errorText(e))
  } finally {
    saving.value = false
  }
}

function revert(): void {
  if (saved.value) apply(saved.value)
}
</script>

<template>
  <page-head title="形象" />
  <n-spin :show="loading">
    <n-space vertical :size="20">
      <n-card title="当前模型">
        <n-space :wrap="false">
          <n-select v-model:value="path" :options="options" placeholder="选择模型" style="width: 420px" />
          <n-button :loading="importing" @click="importNew">导入模型…</n-button>
        </n-space>
        <p class="hint">导入会把模型文件夹复制进模型库，并自动补全 VTube Studio 模型缺少的眨眼/口型/表情登记。切换模型在保存后重载桌宠。</p>
      </n-card>

      <n-card title="构图与缩放">
        <n-form label-placement="left" label-width="90">
          <n-form-item label="构图">
            <n-radio-group v-model:value="framing">
              <n-radio-button value="full">全身</n-radio-button>
              <n-radio-button value="half">半身</n-radio-button>
            </n-radio-group>
          </n-form-item>
          <n-form-item v-if="framing === 'half'" label="显示比例">
            <n-slider
              v-model:value="halfRatio"
              :min="0.2"
              :max="1"
              :step="0.01"
              :format-tooltip="(v: number) => `上方 ${Math.round(v * 100)}%`"
              style="width: 300px"
            />
            <span class="value">{{ Math.round(halfRatio * 100) }}%</span>
          </n-form-item>
          <n-form-item label="缩放">
            <n-space align="center">
              <n-radio-group v-model:value="scaleMode">
                <n-radio-button value="auto">自动适配</n-radio-button>
                <n-radio-button value="custom">自定义</n-radio-button>
              </n-radio-group>
              <n-input-number
                v-if="scaleMode === 'custom'"
                v-model:value="customScale"
                :min="0.01"
                :max="10"
                :step="0.01"
                :precision="3"
                style="width: 130px"
              />
            </n-space>
          </n-form-item>
        </n-form>
        <n-alert type="info" :show-icon="false">
          半身模式只显示模型上方的一部分并自动放大填满窗口；调整会实时预览到桌宠，未保存就关闭控制台会恢复原样。
        </n-alert>
        <template #action>
          <n-space justify="end">
            <n-button :disabled="!dirty" @click="revert">撤销修改</n-button>
            <n-button type="primary" :disabled="!dirty" :loading="saving" @click="save">保存</n-button>
          </n-space>
        </template>
      </n-card>

      <actions-card :model-key="saved?.path ?? ''" />
    </n-space>
  </n-spin>
</template>

<style scoped>
.hint {
  margin: 12px 0 0;
}
.value {
  margin-left: 12px;
  opacity: 0.7;
}
</style>
