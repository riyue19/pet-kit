<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import {
  NAlert,
  NButton,
  NCard,
  NCheckbox,
  NInput,
  NInputNumber,
  NModal,
  NRadioButton,
  NRadioGroup,
  NSpace,
  NSpin,
  NSwitch,
  useMessage
} from 'naive-ui'
import BackendOffline from '../components/BackendOffline.vue'
import PageHead from '../components/PageHead.vue'
import SaveBar from '../components/SaveBar.vue'
import { errorText } from '../store'
import {
  BUILTIN_INFO,
  builtinNames,
  loadInstalled,
  loadSkills,
  makeTargetId,
  saveSkills,
  skillNames,
  SKILL_TITLES,
  TARGET_ID_RE,
  validateSkills,
  type InstalledApp,
  type SkillsConfig,
  type SkillsState,
  type SkillTarget
} from '../skills'

let uid = 0
interface Row {
  key: number
  id: string
  isNew: boolean
  argsText: string
  t: SkillTarget
}
interface Block {
  enabled: boolean
  confirm: boolean
  rows: Row[]
}

const message = useMessage()
const loading = ref(false)
const loaded = ref(false)
const offline = ref(false)
const error = ref<string | null>(null)
const saving = ref(false)
const snapshot = ref('')

const enabled = ref(true)
const timeout = ref<number | null>(30)
const blocks = reactive<Record<string, Block>>({})
// Names of the built-in actions, from the backend.
const builtins = ref<string[]>([])

const nullable = (s: string | null): string | null => (s !== null && s.trim() !== '' ? s : null)

function toConfig(): SkillsConfig {
  const skills: SkillsConfig['skills'] = {}
  for (const name of Object.keys(blocks)) {
    const b = blocks[name]
    const targets: Record<string, SkillTarget> = {}
    for (const r of b.rows) {
      targets[r.id] = {
        label: r.t.label,
        exe: nullable(r.t.exe),
        args: r.argsText.split(/\r?\n/).map((a) => a.trim()).filter((a) => a !== ''),
        url: nullable(r.t.url),
        search_url: nullable(r.t.search_url),
        credential: nullable(r.t.credential)
      }
    }
    skills[name] = { enabled: b.enabled, confirm: b.confirm, targets }
  }
  return { enabled: enabled.value, confirm_timeout_seconds: timeout.value ?? 0, skills }
}

const dirty = computed(() => loaded.value && JSON.stringify(toConfig()) !== snapshot.value)

// The last loaded / saved state, for 撤销修改.
let lastState: SkillsState | null = null

function revert(): void {
  if (lastState) apply(lastState)
}

function apply(state: SkillsState): void {
  lastState = state
  enabled.value = state.enabled
  timeout.value = state.confirm_timeout_seconds
  for (const k of Object.keys(blocks)) delete blocks[k]
  builtins.value = builtinNames(state)
  // Built-in actions always get a row: one that is not in the file is on, without confirmation.
  for (const name of builtins.value) {
    const s = state.skills[name]
    blocks[name] = { enabled: s?.enabled ?? true, confirm: s?.confirm ?? false, rows: [] }
  }
  for (const name of skillNames(state)) {
    const s = state.skills[name]
    if (!s) continue
    blocks[name] = {
      enabled: s.enabled,
      confirm: s.confirm,
      rows: Object.entries(s.targets).map(([id, t]) => ({ key: ++uid, id, isNew: false, argsText: t.args.join('\n'), t: { ...t, args: [...t.args] } }))
    }
  }
  snapshot.value = JSON.stringify(toConfig())
  loaded.value = true
}

async function load(): Promise<void> {
  loading.value = true
  try {
    apply(await loadSkills())
    offline.value = false
    error.value = null
  } catch (e) {
    // Only a refused connection means "offline"; e.g. a 404 is a running backend that predates this page.
    offline.value = (e as { offline?: boolean }).offline === true
    error.value = offline.value ? errorText(e) : `${errorText(e)}（后端已连接但没有这个接口：多半是旧版后端还在运行，请退出桌宠后重新启动，让它换成新版后端）`
  } finally {
    loading.value = false
  }
}

onMounted(() => void load())

async function onSave(): Promise<void> {
  const cfg = toConfig()
  const bad = validateSkills(cfg)
  if (bad) {
    message.error(bad, { duration: 6000 })
    return
  }
  saving.value = true
  try {
    apply(await saveSkills(cfg))
    message.success('已保存')
  } catch (e) {
    message.error(errorText(e), { duration: 6000 })
  } finally {
    saving.value = false
  }
}

const Q = '{query}'
const S = '{secret}'
const PH_SEARCH = '例如 orpheus://search?keyword={query}'
const PH_EXE = '程序完整路径，如 C:/Program Files/app/app.exe'
const PH_URL = '例如 steam://rungameid/12345'
const argsSize = { minRows: 1, maxRows: 6 }
const blockList = computed(() => Object.keys(blocks).filter((name) => !builtins.value.includes(name)).map((name) => ({ name, b: blocks[name] })))
const builtinList = computed(() =>
  builtins.value.filter((name) => name in blocks).map((name) => ({ name, b: blocks[name], info: BUILTIN_INFO[name] ?? { title: name, desc: '' } }))
)
const title = (name: string): string => SKILL_TITLES[name] ?? name

function kindOf(t: SkillTarget): 'exe' | 'url' {
  return t.url !== null && t.exe === null ? 'url' : 'exe'
}
function isKind(t: SkillTarget, k: string): boolean {
  return kindOf(t) === k
}
function setKind(t: SkillTarget, k: string): void {
  if (k === 'url') {
    t.url = t.url ?? ''
    t.exe = null
  } else {
    t.exe = t.exe ?? ''
    t.url = null
  }
}
function idInvalid(r: Row): boolean {
  return r.isNew && r.id !== '' && !TARGET_ID_RE.test(r.id)
}

function addTarget(name: string): void {
  const rows = blocks[name].rows
  rows.push({
    key: ++uid,
    id: '',
    isNew: true,
    argsText: '',
    t: { label: '', exe: '', args: [], url: null, search_url: null, credential: null }
  })
}
function removeTarget(name: string, row: Row): void {
  const rows = blocks[name].rows
  rows.splice(rows.indexOf(row), 1)
}

// "Add from installed programs"
const pickerOpen = ref(false)
const scanning = ref(false)
const installed = ref<InstalledApp[]>([])
const search = ref('')
const picked = ref<string[]>([])

const existingExes = computed(() => new Set((blocks.open_app?.rows ?? []).map((r) => (r.t.exe ?? '').toLowerCase())))
const filtered = computed(() => {
  const q = search.value.trim().toLowerCase()
  return installed.value.filter((a) => !q || a.name.toLowerCase().includes(q) || a.exe.toLowerCase().includes(q))
})
const isTaken = (a: InstalledApp): boolean => existingExes.value.has(a.exe.toLowerCase())

async function openPicker(): Promise<void> {
  scanning.value = true
  try {
    installed.value = await loadInstalled()
    search.value = ''
    picked.value = []
    pickerOpen.value = true
  } catch (e) {
    message.error(errorText(e), { duration: 6000 })
  } finally {
    scanning.value = false
  }
}
function togglePick(a: InstalledApp, on: boolean): void {
  picked.value = on ? [...picked.value, a.exe] : picked.value.filter((x) => x !== a.exe)
}
function confirmPick(): void {
  const rows = blocks.open_app.rows
  for (const a of installed.value) {
    if (!picked.value.includes(a.exe) || isTaken(a)) continue
    const id = makeTargetId(a.exe, rows.map((r) => r.id))
    rows.push({ key: ++uid, id, isNew: true, argsText: '', t: { label: a.name.slice(0, 32), exe: a.exe, args: [], url: null, search_url: null, credential: null } })
  }
  pickerOpen.value = false
}
</script>

<template>
  <page-head title="指令" />
  <n-spin :show="loading && !loaded">
    <backend-offline v-if="offline" :error="error" :loading="loading" @retry="load" />
    <n-alert v-else-if="!loaded && error" type="error" title="加载失败">{{ error }}</n-alert>
    <n-space v-else-if="loaded" vertical :size="20">
      <span class="hint">只有主人和桌宠说话时才提供技能；技能只能从这里的白名单里选。</span>

      <n-card title="总设置">
        <n-space vertical :size="14">
          <n-space align="center">
            <n-switch v-model:value="enabled" />
            <span>启用技能</span>
          </n-space>
          <span class="hint">关闭后 LLM 不会知道任何技能。</span>
          <n-space align="center">
            <span>弹窗确认的等待时间（秒）</span>
            <n-input-number v-model:value="timeout" :min="5" :max="300" :step="5" :precision="0" style="width: 130px" />
          </n-space>
          <span class="hint">本页的修改要点页面底部的「保存」才生效。</span>
        </n-space>
      </n-card>

      <n-card v-for="blk in blockList" :key="blk.name" :title="title(blk.name)">
        <n-space vertical :size="14">
          <n-space align="center" :size="24">
            <n-space align="center">
              <n-switch v-model:value="blk.b.enabled" />
              <span>启用</span>
            </n-space>
            <n-space align="center">
              <n-switch v-model:value="blk.b.confirm" />
              <span>执行前确认</span>
            </n-space>
          </n-space>
          <span class="hint">开启确认后，执行前桌宠会弹窗，点「允许」才执行{{ blk.name === 'open_game' ? '；开游戏建议开启' : '' }}。</span>

          <div v-if="blk.b.rows.length === 0" class="hint">还没有目标。</div>
          <n-card v-for="r in blk.b.rows" :key="r.key" size="small" embedded>
            <n-space vertical :size="10">
              <n-space align="center">
                <span class="label">id</span>
                <span v-if="!r.isNew">{{ r.id }}</span>
                <n-input v-else v-model:value="r.id" :maxlength="32" placeholder="小写字母开头，如 notepad" :status="idInvalid(r) ? 'error' : undefined" style="width: 240px" />
                <n-button size="small" type="error" quaternary @click="removeTarget(blk.name, r)">删除</n-button>
              </n-space>
              <n-space align="center">
                <span class="label">名称</span>
                <n-input v-model:value="r.t.label" :maxlength="32" placeholder="LLM 看到的名字" style="width: 240px" />
              </n-space>
              <n-space align="center">
                <span class="label">类型</span>
                <n-radio-group :value="kindOf(r.t)" size="small" @update:value="setKind(r.t, $event)">
                  <n-radio-button value="exe">程序 exe</n-radio-button>
                  <n-radio-button value="url">URL scheme</n-radio-button>
                </n-radio-group>
              </n-space>
              <n-input v-if="isKind(r.t, 'exe')" :value="r.t.exe" @update:value="r.t.exe = $event" :placeholder="PH_EXE" />
              <n-input v-if="isKind(r.t, 'url')" :value="r.t.url" @update:value="r.t.url = $event" :placeholder="PH_URL" />
              <div>
                <div class="label-row">参数（一行一个）</div>
                <n-input v-model:value="r.argsText" type="textarea" :autosize="argsSize" />
              </div>
              <template v-if="blk.name === 'play_music'"><div>
                <div class="label-row">搜索地址</div>
                <n-input v-model:value="r.t.search_url" :placeholder="PH_SEARCH" />
                <div class="hint">必须含 {{ Q }}，会替换成歌名；留空则只打开软件。</div>
              </div></template>
              <div v-if="blk.name === 'open_game'">
                <div class="label-row">凭据</div>
                <n-input v-model:value="r.t.credential" placeholder="凭据管理器里的用户名" />
                <div class="hint">
                  Windows 凭据管理器里服务名 pet-kit/skill 下的用户名，其密码会替换参数中的 {{ S }}。注意：命令行参数本机其他进程可见。
                </div>
              </div>
            </n-space>
          </n-card>

          <n-space>
            <n-button size="small" @click="addTarget(blk.name)">添加目标</n-button>
            <n-button v-if="blk.name === 'open_app'" size="small" :loading="scanning" @click="openPicker">从已安装程序添加</n-button>
          </n-space>
        </n-space>
      </n-card>
      <n-card title="内置动作">
        <n-space vertical :size="14">
          <span class="hint">这几样不启动任何程序，只在桌宠自己身上做事。默认都开着；不想让它做哪一样就关掉，或者打开「执行前确认」让它先弹窗问你。</span>
          <div v-for="x in builtinList" :key="x.name" class="builtin">
            <div class="builtin-text">
              <div>{{ x.info.title }}</div>
              <div class="hint">{{ x.info.desc }}</div>
            </div>
            <n-space align="center" :size="20" :wrap="false">
              <n-space align="center" :wrap="false">
                <n-switch v-model:value="x.b.enabled" />
                <span>启用</span>
              </n-space>
              <n-space align="center" :wrap="false">
                <n-switch v-model:value="x.b.confirm" :disabled="!x.b.enabled" />
                <span>执行前确认</span>
              </n-space>
            </n-space>
          </div>
        </n-space>
      </n-card>
    </n-space>
  </n-spin>
  <save-bar v-if="loaded && !offline" :dirty="dirty" :saving="saving" @save="onSave" @revert="revert" />

  <n-modal v-model:show="pickerOpen" preset="card" title="从已安装程序添加" style="width: 560px" :mask-closable="false">
    <n-space vertical :size="12">
      <n-input v-model:value="search" clearable placeholder="搜索名称或路径" />
      <div class="list">
        <div v-for="a in filtered" :key="a.exe" class="item" :class="{ taken: isTaken(a) }">
          <n-checkbox :checked="picked.includes(a.exe)" :disabled="isTaken(a)" @update:checked="togglePick(a, $event)">
            <div>{{ a.name }}</div>
            <div class="hint">{{ a.exe }}{{ isTaken(a) ? '（已在白名单）' : '' }}</div>
          </n-checkbox>
        </div>
        <div v-if="filtered.length === 0" class="hint">没有匹配的程序。</div>
      </div>
      <n-space justify="end">
        <n-button @click="pickerOpen = false">取消</n-button>
        <n-button type="primary" :disabled="picked.length === 0" @click="confirmPick">添加 {{ picked.length }} 项</n-button>
      </n-space>
    </n-space>
  </n-modal>
</template>

<style scoped>
.label {
  min-width: 3em;
  opacity: 0.8;
}
.label-row {
  margin-bottom: 6px;
}
.list {
  max-height: 360px;
  overflow-y: auto;
}
.item {
  padding: 6px 4px;
}
.item.taken {
  opacity: 0.45;
}
.builtin {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  padding-bottom: 12px;
  border-bottom: 1px solid rgba(128, 128, 128, 0.2);
}
.builtin:last-child {
  padding-bottom: 0;
  border-bottom: 0;
}
.builtin-text {
  flex: 1;
  min-width: 240px;
}
</style>
