<script setup lang="ts">
import { computed, ref } from 'vue'
import {
  NButton,
  NCard,
  NCollapse,
  NCollapseItem,
  NColorPicker,
  NForm,
  NFormItem,
  NInput,
  NRadioButton,
  NRadioGroup,
  NSelect,
  NSlider,
  NSpace,
  useMessage
} from 'naive-ui'
import type { UiSettings } from '../../../shared/ipc'
import CssEditor from '../components/CssEditor.vue'
import PageHead from '../components/PageHead.vue'
import SaveBar from '../components/SaveBar.vue'
import { errorText, ui } from '../store'
import { clone } from '../util'
import {
  BUBBLE_STYLES,
  CONSOLE_STYLES,
  INPUT_STYLES,
  MAX_SCHEMES,
  MAX_SCHEME_NAME_CHARS,
  type StyleFields
} from '../../../shared/ui-style'
import '../../pet-style.css'

const PET_CSS_EXAMPLE = '#bubble {\n  --b-bg: #fff3c4;\n  --b-fg: #5a4300;\n  font-size: 15px;\n}\n#chat-input { --i-radius: 8px; }'

const FONT_MIN = 12
const FONT_MAX = 20
const SWATCHES = ['#8c6cff', '#18a058', '#2080f0', '#f0a020', '#d03050', '#ff6fa8']

const message = useMessage()
const saved = ref<UiSettings>({ ...ui.value })
const saving = ref(false)
// The menu order is saved on its own (dragging in App.vue), so it never counts as an unsaved change here.
const comparable = (v: UiSettings): string => JSON.stringify({ ...v, navOrder: null })
const dirty = computed(() => comparable(ui.value) !== comparable(saved.value))

window.petConsole.getUi().then((v) => {
  saved.value = v
})

async function save(): Promise<void> {
  saving.value = true
  try {
    // A plain copy: Electron IPC cannot clone Vue's reactive proxies (including nested ones such as `styles`).
    saved.value = await window.petConsole.saveUi(clone(ui.value))
    ui.value = { ...saved.value }
    message.success('已保存')
  } catch (e) {
    message.error(errorText(e))
  } finally {
    saving.value = false
  }
}

// --- Style schemes: named snapshots of the five look fields. Every action here is written to disk at once. ---
const schemeName = ref('')
const selected = ref<string | null>(null)
const schemeOptions = computed(() => ui.value.styles.map((x) => ({ label: x.name, value: x.name })))

function currentFields(): StyleFields {
  const { consoleStyle, inputStyle, bubbleStyle, petCss, consoleCss } = ui.value
  return { consoleStyle, inputStyle, bubbleStyle, petCss, consoleCss }
}

async function saveScheme(): Promise<void> {
  const name = schemeName.value.trim()
  if (!name) return void message.warning('先给方案起个名字')
  const exists = ui.value.styles.some((x) => x.name === name)
  if (!exists && ui.value.styles.length >= MAX_SCHEMES) return void message.warning(`最多保存 ${MAX_SCHEMES} 个方案`)
  const next = { name, ...currentFields() }
  ui.value.styles = exists ? ui.value.styles.map((x) => (x.name === name ? next : x)) : [...ui.value.styles, next]
  selected.value = name
  await save()
}

async function applyScheme(): Promise<void> {
  const scheme = ui.value.styles.find((x) => x.name === selected.value)
  if (!scheme) return
  const { name: _name, ...fields } = scheme
  Object.assign(ui.value, fields)
  schemeName.value = scheme.name
  await save()
}

async function deleteScheme(): Promise<void> {
  if (!selected.value) return
  ui.value.styles = ui.value.styles.filter((x) => x.name !== selected.value)
  selected.value = null
  await save()
}

function revert(): void {
  // The menu order is saved on its own (dragging in App.vue), not by this page.
  ui.value = { ...saved.value, navOrder: ui.value.navOrder }
}
</script>

<template>
  <page-head title="界面" />
  <n-card title="控制台外观">
    <n-form label-placement="left" label-width="90">
      <n-form-item label="主题">
        <n-radio-group v-model:value="ui.theme">
          <n-radio-button value="auto">跟随系统</n-radio-button>
          <n-radio-button value="light">浅色</n-radio-button>
          <n-radio-button value="dark">深色</n-radio-button>
        </n-radio-group>
      </n-form-item>
      <n-form-item label="强调色">
        <n-color-picker
          v-model:value="ui.accent"
          :modes="['hex']"
          :show-alpha="false"
          :swatches="SWATCHES"
          style="width: 200px"
        />
      </n-form-item>
      <n-form-item label="字号">
        <n-slider v-model:value="ui.fontSize" :min="FONT_MIN" :max="FONT_MAX" :step="1" style="width: 260px" />
        <span class="hint">{{ ui.fontSize }} px</span>
      </n-form-item>
      <n-form-item label="控制台样式">
        <n-radio-group v-model:value="ui.consoleStyle">
          <n-radio-button v-for="p in CONSOLE_STYLES" :key="p.id" :value="p.id">{{ p.label }}</n-radio-button>
        </n-radio-group>
      </n-form-item>
      <n-form-item label="自定义 CSS">
        <div class="css-block">
          <css-editor
            v-model="ui.consoleCss"
            :rows="4"
            title="控制台自定义 CSS"
            placeholder="例：.n-card { box-shadow: 0 2px 12px #0003; }（输入即生效；若界面被弄丢，不要保存直接关闭窗口重开，或手改 config/console.yaml 清空 console_css）"
          />
          <div class="hint-block">会写 CSS 的人用；不填不影响使用。输入即生效，点底部「保存」后才写入。</div>
          <n-collapse class="css-help">
            <n-collapse-item title="「状态」页进度条的类名和变量" name="console-css">
              <div class="hint-block">
                整条是 <code>.status-bar</code>（每个值带 <code>data-stat="id"</code>，好感度是 <code>affection</code>），
                轨道 <code>.sb-track</code>、填充 <code>.sb-fill</code>、阶段标签 <code>.sb-tier</code>、数值 <code>.sb-value</code>。
                可改的变量：<code>--sb-height --sb-radius --sb-track --sb-fill --sb-glow</code>。
                例：<code>.status-bar { --sb-height: 22px; --sb-radius: 4px; }</code>
              </div>
            </n-collapse-item>
          </n-collapse>
        </div>
      </n-form-item>
    </n-form>
  </n-card>

  <n-card title="桌宠输入框与气泡" style="margin-top: 20px">
    <n-form label-placement="left" label-width="90">
      <n-form-item label="气泡">
        <n-radio-group v-model:value="ui.bubbleStyle">
          <n-radio-button v-for="p in BUBBLE_STYLES" :key="p.id" :value="p.id">{{ p.label }}</n-radio-button>
        </n-radio-group>
      </n-form-item>
      <n-form-item label="输入框">
        <n-radio-group v-model:value="ui.inputStyle">
          <n-radio-button v-for="p in INPUT_STYLES" :key="p.id" :value="p.id">{{ p.label }}</n-radio-button>
        </n-radio-group>
      </n-form-item>
      <n-form-item label="预览">
        <div class="preview" :data-bubble-style="ui.bubbleStyle" :data-input-style="ui.inputStyle">
          <div class="pet-bubble sample-bubble">你好呀，今天想聊点什么？</div>
          <div class="status-pop up sample-pop">
            <div class="sp-head"><div class="sp-icon">▲</div><div class="sp-name">好感度</div><div class="sp-delta">+0.3</div></div>
            <div class="sp-bar"><i style="width: 42%" /></div>
            <div class="sp-reason">被夸了很开心</div>
          </div>
          <input class="pet-input sample-input" placeholder="和我说点什么…（回车发送）" readonly />
        </div>
      </n-form-item>
      <n-form-item label="自定义 CSS">
        <div class="css-block">
          <css-editor
            v-model="ui.petCss"
            :rows="6"
            title="桌宠自定义 CSS"
            :placeholder="PET_CSS_EXAMPLE"
          />
          <div class="hint-block">会写 CSS 的人用；不填不影响使用。作用于桌宠窗口，保存后立即生效（上面的预览不含自定义 CSS）。出错时清空即可恢复。</div>
          <n-collapse class="css-help">
            <n-collapse-item title="气泡、输入框、状态提示的类名和变量" name="pet-css">
              <div class="hint-block">
                气泡是 <code>#bubble</code>，输入框是 <code>#chat-input</code>；用 id 选择器可以盖过上面的预设。
                可改的变量：<code>--b-bg --b-fg --b-border --b-radius --b-shadow</code>（气泡）、
                <code>--i-bg --i-bg-active --i-fg --i-ph --i-border --i-radius</code>（输入框）。
                状态值变化时头部右侧飘出的提示是 <code>#status-fx .status-pop</code>（上升 <code>.up</code>、下降 <code>.down</code>、进入新阶段 <code>.tier</code>，
                每个值带 <code>data-stat="id"</code>），里面有 <code>.sp-name .sp-delta .sp-bar .sp-reason .sp-tier</code>；
                变量：<code>--s-bg --s-fg --s-radius --s-shadow --s-down</code>，例：<code>#status-fx .status-pop { --s-bg: #2a2a33; --s-fg: #fff; }</code>。
              </div>
            </n-collapse-item>
          </n-collapse>
        </div>
      </n-form-item>
    </n-form>
  </n-card>

  <n-card title="样式方案" style="margin-top: 20px">
    <n-form label-placement="left" label-width="90">
      <n-form-item label="已保存的方案">
        <n-space vertical :size="8" style="width: 100%">
          <n-space :size="8">
            <n-select
              v-model:value="selected"
              :options="schemeOptions"
              placeholder="还没有方案"
              :disabled="schemeOptions.length === 0"
              style="width: 240px"
            />
            <n-button :disabled="!selected" :loading="saving" @click="applyScheme">应用</n-button>
            <n-button :disabled="!selected" :loading="saving" type="error" secondary @click="deleteScheme">删除</n-button>
          </n-space>
          <span class="hint-block">应用会替换下面这五项：控制台样式与 CSS、输入框与气泡样式、桌宠 CSS，并立即保存。</span>
        </n-space>
      </n-form-item>
      <n-form-item label="保存当前样式">
        <n-space :size="8">
          <n-input v-model:value="schemeName" :maxlength="MAX_SCHEME_NAME_CHARS" placeholder="方案名称" style="width: 240px" />
          <n-button type="primary" :loading="saving" @click="saveScheme">保存为方案</n-button>
        </n-space>
        <span class="hint">同名则覆盖</span>
      </n-form-item>
    </n-form>
  </n-card>
  <save-bar :dirty="dirty" :saving="saving" hint="外观的修改先实时预览，点「保存」后才写入；样式方案的操作立即保存" @save="save" @revert="revert" />
</template>

<style scoped>
.hint {
  margin-left: 12px;
}
.css-help {
  margin-top: 8px;
}
.css-block {
  width: 100%;
}
.hint-block {
  margin-top: 6px;
  font-size: 0.9em;
  line-height: 1.6;
  opacity: 0.7;
}
.preview {
  width: 100%;
  box-sizing: border-box;
  padding: 18px 20px;
  border-radius: 8px;
  background: linear-gradient(135deg, #6a8fd8, #d58fc4 55%, #f3b76b);
}
/* Same look as the pet window's elements, but in normal flow. */
.sample-bubble {
  position: relative;
  display: inline-block;
  max-width: 80%;
  margin-bottom: 14px;
  padding: 8px 12px;
  box-sizing: border-box;
  font: 13px/1.5 'Microsoft YaHei UI', sans-serif;
}
.sample-pop {
  display: inline-block;
  margin: 0 0 14px 12px;
  vertical-align: top;
}
.sample-input {
  display: block;
  width: 100%;
  height: 34px;
  padding: 0 14px;
  box-sizing: border-box;
  outline: none;
  font: 13px 'Microsoft YaHei UI', sans-serif;
}
</style>
