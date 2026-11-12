<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { NButton, NCard, NCheckbox, NEmpty, NInput, NInputNumber, NSelect, NSpace, NSpin, useMessage } from 'naive-ui'
import { HOLD_SECONDS_RANGE, LABEL_RE } from '../../../shared/agent'
import type { ActionMap, ModelActions, ModelInventory, MotionRef } from '../../../shared/agent'
import { errorText } from '../store'

interface Row {
  id: number
  label: string
  value: string
  /** Motion rows only: expression shown while the motion plays. */
  expr?: string | null
  /** Motion rows only: keep restarting until the user engages the pet. */
  loop?: boolean
}

const EXPRESSION_PRESETS = ['happy', 'sad', 'angry', 'surprised', 'shy', 'confused']
const MOTION_PRESETS = ['wave', 'nod', 'shake_head', 'jump']

const props = defineProps<{ modelKey: string }>()

const message = useMessage()
const loading = ref(true)
const saving = ref(false)
const hasModel = ref(false)
const available = ref<ModelInventory>({ expressions: [], motions: [] })
const expressionRows = ref<Row[]>([])
const motionRows = ref<Row[]>([])
const holdSeconds = ref(6)
const snapshot = ref('')

let nextId = 1
let loadSeq = 0

const expressionOptions = computed(() => available.value.expressions.map((name) => ({ label: name, value: name })))
const motionOptions = computed(() =>
  available.value.motions.flatMap(({ group, count }) =>
    Array.from({ length: count }, (_, i) => ({ label: `${group} #${i + 1}`, value: `${group}:${i}` }))
  )
)

function encodeMotion(group: string, index: number): string {
  return `${group}:${index}`
}

function decodeMotion(value: string): { group: string; index: number } {
  const i = value.lastIndexOf(':')
  return { group: value.slice(0, i), index: Number(value.slice(i + 1)) }
}

function motionOf(row: Row): MotionRef {
  return { ...decodeMotion(row.value), ...(row.expr ? { expression: row.expr } : {}), ...(row.loop ? { loop: true } : {}) }
}

function toRows(map: ActionMap): void {
  expressionRows.value = Object.entries(map.expressions).map(([label, value]) => ({ id: nextId++, label, value }))
  motionRows.value = Object.entries(map.motions).map(([label, ref]) => ({
    id: nextId++,
    label,
    value: encodeMotion(ref.group, ref.index),
    expr: ref.expression ?? null,
    loop: ref.loop === true
  }))
  holdSeconds.value = map.holdSeconds
}

function toMap(): ActionMap {
  const expressions: Record<string, string> = {}
  for (const r of expressionRows.value) if (r.label && r.value) expressions[r.label] = r.value
  const motions: ActionMap['motions'] = {}
  for (const r of motionRows.value) if (r.label && r.value) motions[r.label] = motionOf(r)
  return { expressions, motions, holdSeconds: holdSeconds.value }
}

/** Row-level snapshot so that adding an empty row also counts as a change. */
function currentState(): string {
  return JSON.stringify({
    e: expressionRows.value.map((r) => [r.label, r.value]),
    m: motionRows.value.map((r) => [r.label, r.value, r.expr ?? null, r.loop === true]),
    h: holdSeconds.value
  })
}

function takeSnapshot(): void {
  snapshot.value = currentState()
}

async function load(): Promise<void> {
  const seq = ++loadSeq
  loading.value = true
  try {
    const data: ModelActions = await window.petConsole.modelActions()
    if (seq !== loadSeq) return
    hasModel.value = data.model !== null
    available.value = data.available
    toRows(data.map)
    takeSnapshot()
  } catch (e) {
    if (seq === loadSeq) message.error(errorText(e))
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

onMounted(load)
watch(() => props.modelKey, load)

function labelError(rows: Row[], row: Row): boolean {
  if (!LABEL_RE.test(row.label)) return true
  return rows.some((r) => r !== row && r.label === row.label)
}

function valueError(row: Row): boolean {
  return !row.value
}

const hasErrors = computed(
  () =>
    [expressionRows.value, motionRows.value].some((rows) =>
      rows.some((r) => labelError(rows, r) || valueError(r))
    ) || typeof holdSeconds.value !== 'number'
)

const dirty = computed(() => currentState() !== snapshot.value)

function addRow(rows: Row[], label = ''): void {
  rows.push({ id: nextId++, label, value: '' })
}

function removeRow(rows: Row[], row: Row): void {
  const i = rows.indexOf(row)
  if (i >= 0) rows.splice(i, 1)
}

function missingPresets(presets: string[], rows: Row[]): string[] {
  return presets.filter((p) => !rows.some((r) => r.label === p))
}

const missingExpressionPresets = computed(() => missingPresets(EXPRESSION_PRESETS, expressionRows.value))
const missingMotionPresets = computed(() => missingPresets(MOTION_PRESETS, motionRows.value))

function playExpression(row: Row): void {
  if (row.value) window.petConsole.playAction({ expression: row.value })
}

function playMotion(row: Row): void {
  if (!row.value) return
  window.petConsole.playAction({ motion: motionOf(row) })
}

function stopMotion(): void {
  window.petConsole.playAction({ stop: true })
}

function revert(): void {
  const data = JSON.parse(snapshot.value) as { e: [string, string][]; m: [string, string, string | null, boolean][]; h: number }
  expressionRows.value = data.e.map(([label, value]) => ({ id: nextId++, label, value }))
  motionRows.value = data.m.map(([label, value, expr, loop]) => ({ id: nextId++, label, value, expr, loop }))
  holdSeconds.value = data.h
}

async function save(): Promise<void> {
  if (hasErrors.value) return
  saving.value = true
  try {
    const result = await window.petConsole.saveModelActions(toMap())
    toRows(result)
    takeSnapshot()
    message.success('已保存，桌宠已更新')
  } catch (e) {
    message.error(errorText(e))
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <n-card title="表情与动作">
    <n-spin :show="loading">
      <n-empty v-if="!loading && !hasModel" description="先在上方选择并保存模型" />
      <div v-else-if="hasModel">
        <p class="hint">
          LLM 只会使用这里定义的标签（小写英文，如 happy）。每个标签对应模型自带的一个表情或动作，点「试播」可在桌宠上预览。标签对应的朗读语气在「语音」页的「表情 → 语气」里调。
        </p>

        <h3 class="section">表情</h3>
        <p v-if="available.expressions.length === 0" class="hint">该模型没有表情文件</p>
        <template v-else>
          <n-space vertical :size="8">
            <n-space v-for="row in expressionRows" :key="row.id" :wrap="false" align="center">
              <n-input
                v-model:value="row.label"
                placeholder="标签"
                :status="labelError(expressionRows, row) ? 'error' : undefined"
                style="width: 140px"
              />
              <n-select
                v-model:value="row.value"
                :options="expressionOptions"
                placeholder="选择表情"
                :status="valueError(row) ? 'error' : undefined"
                style="width: 200px"
              />
              <n-button :disabled="!row.value" @click="playExpression(row)">试播</n-button>
              <n-button quaternary type="error" @click="removeRow(expressionRows, row)">删除</n-button>
            </n-space>
            <n-space align="center">
              <n-button size="small" @click="addRow(expressionRows)">添加</n-button>
              <n-button
                v-for="p in missingExpressionPresets"
                :key="p"
                size="tiny"
                dashed
                @click="addRow(expressionRows, p)"
              >
                + {{ p }}
              </n-button>
            </n-space>
          </n-space>
        </template>

        <h3 class="section">动作</h3>
        <p v-if="motionOptions.length === 0" class="hint">该模型没有动作文件</p>
        <template v-else>
          <p class="hint">
            动作播完会自动回到默认姿势。勾上「循环」的动作会一直保持，直到你点它、和它说话或播别的动作。有些模型的动作只驱动某个部件，而那个部件要靠某个表情才显示：这时选上「配套表情」，播动作时自动显示、播完收起；试播没反应多半是这个原因。
          </p>
          <n-space vertical :size="8">
            <n-space v-for="row in motionRows" :key="row.id" :wrap="false" align="center">
              <n-input
                v-model:value="row.label"
                placeholder="标签"
                :status="labelError(motionRows, row) ? 'error' : undefined"
                style="width: 140px"
              />
              <n-select
                v-model:value="row.value"
                :options="motionOptions"
                placeholder="选择动作"
                :status="valueError(row) ? 'error' : undefined"
                style="width: 200px"
              />
              <n-select
                v-if="expressionOptions.length > 0"
                v-model:value="row.expr"
                :options="expressionOptions"
                clearable
                placeholder="配套表情（可选）"
                style="width: 180px"
              />
              <n-checkbox v-model:checked="row.loop">循环</n-checkbox>
              <n-button :disabled="!row.value" @click="playMotion(row)">试播</n-button>
              <n-button quaternary type="error" @click="removeRow(motionRows, row)">删除</n-button>
            </n-space>
            <n-space align="center">
              <n-button size="small" @click="addRow(motionRows)">添加</n-button>
              <n-button size="small" @click="stopMotion">停止当前动作</n-button>
              <n-button
                v-for="p in missingMotionPresets"
                :key="p"
                size="tiny"
                dashed
                @click="addRow(motionRows, p)"
              >
                + {{ p }}
              </n-button>
            </n-space>
          </n-space>
        </template>

        <h3 class="section">表情保持</h3>
        <n-input-number
          v-model:value="holdSeconds"
          :min="HOLD_SECONDS_RANGE.min"
          :max="HOLD_SECONDS_RANGE.max"
          :step="1"
          style="width: 160px"
        >
          <template #suffix>秒</template>
        </n-input-number>
      </div>
    </n-spin>
    <template v-if="hasModel" #action>
      <n-space justify="end">
        <n-button :disabled="!dirty" @click="revert">撤销修改</n-button>
        <n-button type="primary" :disabled="!dirty || hasErrors" :loading="saving" @click="save">保存</n-button>
      </n-space>
    </template>
  </n-card>
</template>

<style scoped>
.hint {
  margin: 0 0 12px;
}
.section {
  margin: 20px 0 10px;
  font-size: 1em;
  font-weight: 600;
}
</style>
