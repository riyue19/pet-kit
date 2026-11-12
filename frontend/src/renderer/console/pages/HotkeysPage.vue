<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import {
  NAlert,
  NButton,
  NCard,
  NCollapse,
  NCollapseItem,
  NDynamicInput,
  NInput,
  NInputNumber,
  NPopconfirm,
  NRadioButton,
  NRadioGroup,
  NSelect,
  NSpace,
  NSpin,
  NSwitch,
  NTag,
  useMessage
} from 'naive-ui'
import {
  acceleratorFromKeyEvent,
  BUILTIN_ACTIONS,
  BUILTIN_LABELS,
  normalizeAccelerator,
  validateHotkeys
} from '../../../shared/hotkeys'
import PageHead from '../components/PageHead.vue'
import SaveBar from '../components/SaveBar.vue'
import { errorText } from '../store'
import { dirty, hotkeys, itemDirty, loadHotkeys, newHotkey, revertHotkeys, saveHotkeysList, statusOf, uidOf } from '../hotkeys'

const message = useMessage()
const saving = ref(false)
const recordingId = ref<string | null>(null)
const recordError = ref<string | null>(null)

const builtinOptions = BUILTIN_ACTIONS.map((a) => ({ label: BUILTIN_LABELS[a], value: a }))
const expressionOptions = computed(() => hotkeys.expressionChoices.map((v) => ({ label: v, value: v })))
const motionOptions = computed(() => hotkeys.motionChoices.map((v) => ({ label: v, value: v })))

onMounted(() => {
  // Re-read unless there are unsaved edits, so hand edits to hotkeys.yaml aren't silently overwritten.
  if (!hotkeys.loaded || !dirty.value) void loadHotkeys()
})

// ---- recording: only active between startRecording and stopRecording ----
function onKeyDown(e: KeyboardEvent): void {
  e.preventDefault()
  e.stopPropagation()
  if (e.repeat) return
  if (e.key === 'Escape' && !e.ctrlKey && !e.altKey && !e.shiftKey && !e.metaKey) {
    stopRecording()
    return
  }
  const acc = acceleratorFromKeyEvent(e)
  if (acc === null) return
  const norm = normalizeAccelerator(acc)
  if (!norm.ok) {
    recordError.value = norm.error
    return
  }
  const target = hotkeys.list.find((h) => h.id === recordingId.value)
  if (target) target.keys = norm.value
  stopRecording()
}

function onBlur(): void {
  stopRecording()
}

function startRecording(id: string): void {
  if (recordingId.value !== null) return
  recordingId.value = id
  recordError.value = null
  window.petConsole.suspendHotkeys(true)
  window.addEventListener('keydown', onKeyDown, true)
  window.addEventListener('blur', onBlur)
}

function stopRecording(): void {
  if (recordingId.value === null) return
  window.removeEventListener('keydown', onKeyDown, true)
  window.removeEventListener('blur', onBlur)
  window.petConsole.suspendHotkeys(false)
  recordingId.value = null
}

onUnmounted(stopRecording)

function addHotkey(): void {
  if (recordingId.value !== null) stopRecording()
  const h = newHotkey()
  hotkeys.list.push(h)
  startRecording(h.id)
}

function removeHotkey(id: string): void {
  if (recordingId.value === id) stopRecording()
  hotkeys.list = hotkeys.list.filter((h) => h.id !== id)
}

function revert(): void {
  stopRecording()
  revertHotkeys()
}

async function save(): Promise<void> {
  stopRecording()
  try {
    validateHotkeys(hotkeys.list)
  } catch (e) {
    message.error(e instanceof Error ? e.message : String(e), { duration: 6000 })
    return
  }
  if (hotkeys.fileError && !window.confirm('hotkeys.yaml 里有无法读取的项，保存会把它们删除。确定继续吗？')) return
  saving.value = true
  try {
    const state = await saveHotkeysList(hotkeys.list)
    message.success('已保存，快捷键已重新注册')
    const failed = state.hotkeys.filter((h) => h.enabled && !state.status.find((s) => s.id === h.id)?.ok).length
    if (failed > 0) message.warning(`有 ${failed} 个快捷键没有注册成功，见列表中的提示`, { duration: 6000 })
  } catch (e) {
    message.error(errorText(e), { duration: 6000 })
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <page-head title="快捷键" />
  <n-spin :show="hotkeys.loading && !hotkeys.loaded">
    <n-space vertical :size="16">
      <div>
        <div>快捷键在任何程序里都能用。只有这里列出的组合键会注册到系统，其他按键不会被读取或记录。保存后立即生效。</div>
        <div v-if="hotkeys.file" class="hint small">{{ hotkeys.file }}</div>
      </div>
      <div v-if="hotkeys.error" class="error">读取失败：{{ hotkeys.error }}</div>
      <n-alert v-if="hotkeys.fileError" type="warning" title="hotkeys.yaml 有问题">
        {{ hotkeys.fileError }}
        <div class="hint small">下面只列出了能读取的项。保存会按下面的列表重写文件，无效的项会被删除——想保留它们，请先手动修好文件再点「重新读取」。</div>
        <n-button size="small" style="margin-top: 6px" @click="loadHotkeys()">重新读取</n-button>
      </n-alert>

      <template v-if="hotkeys.loaded">
        <n-card v-for="h in hotkeys.list" :key="uidOf(h)" size="small">
          <n-space vertical :size="12">
            <n-space align="center" :wrap="true">
              <n-switch v-model:value="h.enabled" />
              <n-input v-model:value="h.name" :maxlength="32" show-count placeholder="名称" style="width: 220px" />
              <span class="keys">
                <template v-if="h.keys">
                  <kbd v-for="(k, i) in h.keys.split('+')" :key="i">{{ k }}</kbd>
                </template>
                <span v-else class="hint">未设置</span>
              </span>
              <n-button size="small" :type="recordingId === h.id ? 'warning' : 'default'" :disabled="recordingId !== null && recordingId !== h.id" @click="recordingId === h.id ? stopRecording() : startRecording(h.id)">
                {{ recordingId === h.id ? '请按下组合键…（Esc 取消）' : '录制' }}
              </n-button>
              <n-tag v-if="statusOf(h.id)?.ok" type="success" size="small" :bordered="false">已生效</n-tag>
              <n-tag v-else-if="statusOf(h.id)" type="warning" size="small" :bordered="false">{{ statusOf(h.id)?.error }}</n-tag>
              <n-tag v-if="itemDirty(h)" size="small" :bordered="false">未保存</n-tag>
              <n-popconfirm @positive-click="removeHotkey(h.id)">
                <template #trigger>
                  <n-button size="small" quaternary type="error">删除</n-button>
                </template>
                确定删除这个快捷键？
              </n-popconfirm>
            </n-space>
            <div v-if="recordingId === h.id && recordError" class="error">{{ recordError }}</div>

            <n-radio-group v-model:value="h.action">
              <n-radio-button value="builtin">内置动作</n-radio-button>
              <n-radio-button value="reaction">固定反应（不花钱）</n-radio-button>
              <n-radio-button value="llm">交给 LLM（花钱）</n-radio-button>
            </n-radio-group>

            <n-select v-if="h.action === 'builtin'" v-model:value="h.builtin" :options="builtinOptions" placeholder="选择内置动作" style="max-width: 280px" />

            <template v-else-if="h.action === 'reaction'">
              <span class="hint">每次随机选一句台词、一个表情、一个动作。</span>
              <n-dynamic-input v-model:value="h.lines" :max="50" :on-create="() => ''">
                <template #default="{ index }">
                  <n-input v-model:value="h.lines[index]" :maxlength="200" placeholder="台词" />
                </template>
              </n-dynamic-input>
              <n-select v-model:value="h.expressions" multiple clearable :options="expressionOptions" placeholder="表情（可多选）" />
              <n-select v-model:value="h.motions" multiple clearable :options="motionOptions" placeholder="动作（可多选）" />
            </template>

            <template v-else>
              <span class="hint">告诉桌宠发生了什么，它会结合人设和记忆自由回应。</span>
              <n-input v-model:value="h.prompt" type="textarea" :maxlength="500" show-count :autosize="{ minRows: 2, maxRows: 6 }" placeholder="（主人按下了快捷键，想听你夸夸他）" />
            </template>

            <n-space v-if="h.action !== 'builtin'" align="center">
              <span>冷却（秒）</span>
              <n-input-number v-model:value="h.cooldown_seconds" :min="h.action === 'llm' ? 3 : 0" :max="3600" :step="1" :precision="0" style="width: 140px" />
              <span class="hint">两次触发的最短间隔，防止连按刷费用。</span>
            </n-space>

            <n-collapse>
              <n-collapse-item title="高级" :name="h.id">
                <n-space align="center">
                  <span>id</span>
                  <n-input v-model:value="h.id" :maxlength="32" style="width: 220px" />
                </n-space>
              </n-collapse-item>
            </n-collapse>
          </n-space>
        </n-card>

        <n-button @click="addHotkey">添加快捷键</n-button>

        <span class="hint">被其他软件占用的组合键无法注册（比如 QQ、输入法的快捷键），换一个即可。</span>
      </template>
    </n-space>
  </n-spin>
  <save-bar v-if="hotkeys.loaded" :dirty="dirty" :saving="saving" @save="save" @revert="revert" />
</template>

<style scoped>
.small {
  font-size: 0.8em;
  margin-top: 4px;
}
.keys {
  display: inline-flex;
  gap: 4px;
  align-items: center;
}
kbd {
  padding: 1px 8px;
  border: 1px solid rgba(128, 128, 128, 0.5);
  border-bottom-width: 2px;
  border-radius: 4px;
  font-family: inherit;
  font-size: 0.9em;
}
</style>
