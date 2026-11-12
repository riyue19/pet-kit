<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  NAlert,
  NButton,
  NCard,
  NCheckbox,
  NCode,
  NCollapse,
  NCollapseItem,
  NEmpty,
  NForm,
  NFormItem,
  NInput,
  NInputNumber,
  NModal,
  NPagination,
  NPopconfirm,
  NSlider,
  NSpace,
  NSpin,
  NSwitch,
  NTag,
  useMessage
} from 'naive-ui'
import BackendOffline from '../components/BackendOffline.vue'
import PageHead from '../components/PageHead.vue'
import SaveBar from '../components/SaveBar.vue'
import { errorText } from '../store'
import {
  addItem,
  deleteItem,
  dirty,
  formatTime,
  loadItems,
  loadMemory,
  mem,
  PAGE_SIZES,
  pageItems,
  revertMemory,
  saveMemory,
  summarizeNow,
  summaryText,
  updateItem
} from '../memory'

const message = useMessage()
const saving = ref(false)
const summarizing = ref(false)
const rechecking = ref(false)

const adding = ref(false)
const newText = ref('')
const newPinned = ref(false)
const addBusy = ref(false)

const editingId = ref<number | null>(null)
const editText = ref('')
const editBusy = ref(false)

let timer: ReturnType<typeof setTimeout> | null = null

onMounted(async () => {
  await loadMemory()
  if (mem.loaded) void loadItems()
})

onBeforeUnmount(() => {
  if (timer) clearTimeout(timer)
})

watch(
  () => mem.query,
  () => {
    if (timer) clearTimeout(timer)
    timer = setTimeout(() => void loadItems(true), 300)
  }
)

watch(
  () => mem.semantic,
  () => void loadItems(true)
)

watch(
  () => mem.page,
  () => {
    editingId.value = null
  }
)

async function retry(): Promise<void> {
  await loadMemory()
  if (mem.loaded) void loadItems()
}

async function recheck(): Promise<void> {
  rechecking.value = true
  try {
    await loadMemory(true)
  } catch (e) {
    message.error(errorText(e), { duration: 6000 })
  } finally {
    rechecking.value = false
  }
}

async function summarize(): Promise<void> {
  summarizing.value = true
  try {
    const r = await summarizeNow()
    if (!r.ok) message.error(r.error ?? '总结失败', { duration: 6000 })
    else if (r.lines === 0) message.info('没有待总结的内容')
    else message.success(summaryText(r))
    await loadMemory(true)
    await loadItems()
  } catch (e) {
    message.error(errorText(e), { duration: 6000 })
  } finally {
    summarizing.value = false
  }
}

function startAdd(): void {
  newText.value = ''
  newPinned.value = false
  adding.value = true
}

async function saveAdd(): Promise<void> {
  const text = newText.value.trim()
  if (!text) return
  addBusy.value = true
  try {
    await addItem(text, newPinned.value)
    adding.value = false
    message.success('已添加')
  } catch (e) {
    message.error(errorText(e), { duration: 6000 })
  } finally {
    addBusy.value = false
  }
}

async function togglePin(id: number, pinned: boolean): Promise<void> {
  try {
    await updateItem(id, { pinned: !pinned })
    // Pinned items sort first in the plain list; refresh to get the right order.
    if (!(mem.semantic && mem.query.trim())) await loadItems()
  } catch (e) {
    message.error(errorText(e), { duration: 6000 })
  }
}

function startEdit(id: number, text: string): void {
  editingId.value = id
  editText.value = text
}

function cancelEdit(): void {
  editingId.value = null
}

async function saveEdit(): Promise<void> {
  const id = editingId.value
  const text = editText.value.trim()
  if (id === null || !text) return
  editBusy.value = true
  try {
    await updateItem(id, { text })
    editingId.value = null
  } catch (e) {
    message.error(errorText(e), { duration: 6000 })
  } finally {
    editBusy.value = false
  }
}

async function remove(id: number): Promise<void> {
  try {
    await deleteItem(id)
  } catch (e) {
    message.error(errorText(e), { duration: 6000 })
  }
}

const confirmMove = ref(false)

function dbPathChanged(): boolean {
  let old = ''
  try {
    old = (JSON.parse(mem.snapshot) as { db_path?: string }).db_path ?? ''
  } catch {
    /* ignore */
  }
  return mem.config.db_path.trim() !== old.trim()
}

function onSaveClick(): void {
  if (dbPathChanged()) confirmMove.value = true
  else void save()
}

async function showInFolder(): Promise<void> {
  try {
    const err = await window.petConsole.showFileInFolder(mem.stats.db_path)
    if (err) message.error(err, { duration: 6000 })
  } catch (e) {
    message.error(errorText(e), { duration: 6000 })
  }
}

async function save(): Promise<void> {
  confirmMove.value = false
  saving.value = true
  try {
    const notice = await saveMemory()
    message.success('已保存')
    if (notice) message.info(notice, { duration: 6000 })
    await loadMemory(true)
  } catch (e) {
    message.error(errorText(e), { duration: 6000 })
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <page-head title="记忆" />
  <n-spin :show="mem.loading && !mem.loaded">
    <backend-offline v-if="mem.offline" :error="mem.error" :loading="mem.loading" @retry="retry" />
    <n-alert v-else-if="!mem.loaded && mem.error" type="error">
      {{ mem.error }}
      <div class="actions"><n-button size="small" :loading="mem.loading" @click="retry">重试</n-button></div>
    </n-alert>
    <n-space v-else-if="mem.loaded" vertical :size="20">
      <n-card title="概况">
        <n-space vertical :size="12">
          <n-space :size="24">
            <span>聊天记录 {{ mem.stats.messages }} 条</span>
            <span>长期记忆 {{ mem.stats.memories }} 条</span>
            <span>待总结 {{ mem.stats.unsummarized_turns }} 轮 / 每 {{ mem.config.summarize_every_turns }} 轮总结一次</span>
          </n-space>
          <n-tag v-if="mem.stats.embed_ready" type="success" size="small" :bordered="false" style="align-self: flex-start">语义检索可用</n-tag>
          <n-alert v-else type="warning" title="向量模型未下载，记忆检索暂不可用（聊天记录照常保存）">
            <p class="line">在项目根目录打开终端，执行：</p>
            <n-code :code="`cd backend && ${mem.fetchCommand}`" language="bash" />
            <div class="actions">
              <n-button size="small" :loading="rechecking" @click="recheck">重新检测</n-button>
            </div>
          </n-alert>
          <div>
            <span v-if="!mem.stats.last_summary" class="hint">上次总结：还没有总结过</span>
            <span v-else-if="mem.stats.last_summary.ok">上次总结：{{ summaryText(mem.stats.last_summary) }}</span>
            <span v-else class="error">上次总结失败：{{ mem.stats.last_summary.error }}</span>
          </div>
          <div>
            <n-button :loading="summarizing" @click="summarize">立即总结</n-button>
          </div>
          <n-space align="center" :size="8">
            <span class="hint">聊天记录和记忆都只保存在本机：{{ mem.stats.db_path }}</span>
            <n-button size="tiny" @click="showInFolder">在文件夹中显示</n-button>
          </n-space>
        </n-space>
      </n-card>

      <n-card title="长期记忆">
        <n-space vertical :size="12">
          <n-space align="center">
            <n-input v-model:value="mem.query" clearable placeholder="搜索记忆" style="width: 260px" />
            <n-switch v-model:value="mem.semantic" :disabled="!mem.stats.embed_ready" />
            <span>按意思搜索</span>
            <n-button @click="startAdd">添加记忆</n-button>
          </n-space>
          <span class="hint">置顶的记忆每次对话都会带上；其他记忆只在和当前话题相关时才会被想起。</span>

          <div v-if="adding" class="row">
            <n-space vertical :size="8" style="width: 100%">
              <n-input v-model:value="newText" type="textarea" :maxlength="500" show-count :autosize="{ minRows: 2, maxRows: 5 }" placeholder="要记住的内容" />
              <n-space align="center">
                <n-checkbox v-model:checked="newPinned">置顶</n-checkbox>
                <n-button type="primary" size="small" :disabled="!newText.trim()" :loading="addBusy" @click="saveAdd">保存</n-button>
                <n-button size="small" @click="adding = false">取消</n-button>
              </n-space>
            </n-space>
          </div>

          <span v-if="mem.itemsError" class="error">{{ mem.itemsError }}</span>
          <n-spin :show="mem.itemsLoading">
            <n-empty v-if="mem.items.length === 0 && !mem.itemsError && !mem.itemsLoading" description="还没有长期记忆。聊满一定轮数后会自动总结，也可以手动添加。" />
            <div v-for="it in pageItems" :key="it.id" class="row">
              <template v-if="editingId === it.id">
                <n-space vertical :size="8" style="width: 100%">
                  <n-input
                    v-model:value="editText"
                    type="textarea"
                    :maxlength="500"
                    show-count
                    :autosize="{ minRows: 2, maxRows: 5 }"
                    @keydown.ctrl.enter.prevent="saveEdit"
                    @keydown.esc="cancelEdit"
                  />
                  <n-space>
                    <n-button type="primary" size="small" :disabled="!editText.trim()" :loading="editBusy" @click="saveEdit">保存</n-button>
                    <n-button size="small" @click="cancelEdit">取消</n-button>
                    <span class="hint">回车换行，Ctrl+回车保存，Esc 取消</span>
                  </n-space>
                </n-space>
              </template>
              <template v-else>
                <n-space align="center" :size="8">
                  <n-tag v-if="it.pinned" type="warning" size="small" :bordered="false">置顶</n-tag>
                  <span class="text">{{ it.text }}</span>
                </n-space>
                <n-space align="center" :size="10" style="margin-top: 6px">
                  <n-tag size="small" :bordered="false">{{ it.source === 'summary' ? '自动总结' : '手动' }}</n-tag>
                  <span class="hint">{{ formatTime(it.updated) }}</span>
                  <span v-if="mem.semantic && mem.query.trim() && it.score !== null" class="hint">相似度 {{ it.score.toFixed(2) }}</span>
                  <n-button text size="small" @click="togglePin(it.id, it.pinned)">{{ it.pinned ? '取消置顶' : '置顶' }}</n-button>
                  <n-button text size="small" @click="startEdit(it.id, it.text)">编辑</n-button>
                  <n-popconfirm @positive-click="remove(it.id)">
                    <template #trigger>
                      <n-button text size="small" type="error">删除</n-button>
                    </template>
                    确定删除这条记忆？
                  </n-popconfirm>
                </n-space>
              </template>
            </div>
            <n-space v-if="mem.items.length > PAGE_SIZES[0]" align="center" :size="12" style="margin-top: 12px">
              <n-pagination
                v-model:page="mem.page"
                v-model:page-size="mem.pageSize"
                :item-count="mem.items.length"
                :page-sizes="PAGE_SIZES"
                show-size-picker
                size="small"
                @update:page-size="mem.page = 1"
              />
              <span class="hint">共 {{ mem.items.length }} 条</span>
              <span v-if="mem.items.length === 1000" class="hint">只显示前 1000 条，可用搜索缩小范围</span>
            </n-space>
          </n-spin>
        </n-space>
      </n-card>

      <n-card title="设置">
        <n-form label-placement="left" label-width="150">
          <n-form-item label="使用记忆">
            <n-space vertical :size="6">
              <n-space align="center">
                <n-switch v-model:value="mem.config.enabled" />
                <span>保存聊天记录并使用记忆</span>
              </n-space>
              <span class="hint">关闭后不再保存新的对话，桌宠只记得本次运行中最近的几轮。已有的记录不会被删除。</span>
            </n-space>
          </n-form-item>
          <n-form-item label="总结间隔（轮）">
            <n-space vertical :size="6">
              <n-input-number v-model:value="mem.config.summarize_every_turns" :min="5" :max="200" :step="1" :precision="0" style="width: 130px" />
              <span class="hint">每聊满多少轮（一问一答算一轮），调用一次 LLM 总结成长期记忆。越小记得越及时，调用越多。</span>
            </n-space>
          </n-form-item>
          <n-form-item label="带上几条记忆">
            <n-space vertical :size="6">
              <n-input-number v-model:value="mem.config.recall_memories" :min="0" :max="20" :step="1" :precision="0" style="width: 130px" />
              <span class="hint">每次回复带上几条最相关的记忆（置顶的不占名额）。</span>
            </n-space>
          </n-form-item>
          <n-form-item label="带上几条旧对话">
            <n-space vertical :size="6">
              <n-input-number v-model:value="mem.config.recall_messages" :min="0" :max="10" :step="1" :precision="0" style="width: 130px" />
              <span class="hint">每次回复带上几条相关的旧对话原文。</span>
            </n-space>
          </n-form-item>
          <n-form-item label="最低相似度">
            <n-space vertical :size="6">
              <n-space align="center" :wrap="false">
                <n-slider v-model:value="mem.config.min_similarity" :min="0.1" :max="0.95" :step="0.05" style="width: 200px" />
                <n-input-number v-model:value="mem.config.min_similarity" :min="0.1" :max="0.95" :step="0.05" style="width: 110px" />
              </n-space>
              <span class="hint">越高越严格：只有很相关的才会被想起。</span>
            </n-space>
          </n-form-item>
          <n-form-item label="保存位置">
            <n-space vertical :size="6">
              <n-input v-model:value="mem.config.db_path" clearable placeholder="留空 = 默认位置（%LOCALAPPDATA%\pet-kit）" style="width: 360px" />
              <span class="hint">填一个文件夹（例如 E:\pet-kit-data），保存时会把现有的聊天记录和记忆一起移动过去；如果那里已有 memory.db，就直接改用它。</span>
            </n-space>
          </n-form-item>
        </n-form>
        <n-collapse>
          <n-collapse-item title="高级" name="mem-adv">
            <n-form label-placement="left" label-width="150">
              <n-form-item label="向量模型目录" :show-feedback="false">
                <n-input v-model:value="mem.config.model_dir" style="width: 320px" />
              </n-form-item>
            </n-form>
          </n-collapse-item>
        </n-collapse>
        <div class="hint" style="margin-top: 10px">总结用哪个 LLM，在「AI 模型」页的用途「记忆」里选，不选则沿用聊天模型。</div>
      </n-card>
    </n-space>
  </n-spin>
  <save-bar v-if="mem.loaded && !mem.offline" :dirty="dirty" :saving="saving" hint="长期记忆的增删改立即生效；这里保存的是「设置」" @save="onSaveClick" @revert="revertMemory" />
  <n-modal
    v-model:show="confirmMove"
    preset="dialog"
    title="移动数据库"
    :content="`将把数据库从 ${mem.stats.db_path} 移动到新位置，确定吗？`"
    positive-text="确定"
    negative-text="取消"
    @positive-click="save"
  />
</template>

<style scoped>
.line {
  margin: 0 0 6px;
}
.actions {
  margin-top: 10px;
}
.row {
  padding: 10px 0;
  border-bottom: 1px solid rgba(128, 128, 128, 0.2);
}
.text {
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
