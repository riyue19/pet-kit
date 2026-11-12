<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import {
  NAlert,
  NButton,
  NCard,
  NCollapse,
  NCollapseItem,
  NDynamicTags,
  NInput,
  NInputNumber,
  NModal,
  NRadio,
  NRadioGroup,
  NSelect,
  NSpace,
  NSpin,
  NSwitch,
  NTag,
  useMessage
} from 'naive-ui'
import BackendOffline from '../components/BackendOffline.vue'
import CameraCard from '../components/CameraCard.vue'
import PageHead from '../components/PageHead.vue'
import SaveBar from '../components/SaveBar.vue'
import { cameraDirty, revertCamera, saveCamera } from '../camera'
import { errorText } from '../store'
import {
  cadenceText,
  chatCadenceText,
  cityDirty,
  dirty,
  enablingProactive,
  enablingScreen,
  formatTime,
  loadScreen,
  lookOnce,
  queryWeather,
  refreshStatus,
  revertScreen,
  saveScreen,
  screenState as s
} from '../screen'

const message = useMessage()
const saving = ref(false)
const confirmOpen = ref(false)

const looking = ref(false)
const countdown = ref(0)
const weatherBusy = ref(false)
const weatherText = ref<string | null>(null)
const weatherError = ref<string | null>(null)

const modes: { value: 'quiet' | 'normal' | 'chatty'; label: string }[] = [
  { value: 'quiet', label: '安静' },
  { value: 'normal', label: '适中' },
  { value: 'chatty', label: '话痨' }
]

function labelOptions(all: string[], picked: string[]): { label: string; value: string }[] {
  const opts = all.map((v) => ({ label: v, value: v }))
  for (const v of picked) {
    if (!all.includes(v)) opts.push({ label: `${v}（当前模型没有）`, value: v })
  }
  return opts
}
const motionOptions = computed(() => labelOptions(s.labels.motions, s.proactive.idle_action_motions))
const expressionOptions = computed(() => labelOptions(s.labels.expressions, s.proactive.idle_action_expressions))

let statusTimer: ReturnType<typeof setInterval> | null = null
let countdownTimer: ReturnType<typeof setInterval> | null = null

onMounted(() => {
  if (!s.loaded) void loadScreen()
  statusTimer = setInterval(() => {
    void refreshStatus().catch(() => undefined)
  }, 3000)
})

onUnmounted(() => {
  if (statusTimer) clearInterval(statusTimer)
  if (countdownTimer) clearInterval(countdownTimer)
})

// One save for the whole page: the screen / proactive / privacy settings and the camera card's.
const pageDirty = computed(() => dirty.value || cameraDirty())

async function doSave(): Promise<void> {
  saving.value = true
  try {
    if (dirty.value) await saveScreen()
    if (cameraDirty()) await saveCamera()
    message.success('已保存')
  } catch (e) {
    message.error(errorText(e), { duration: 6000 })
  } finally {
    saving.value = false
  }
}

function revertAll(): void {
  revertScreen()
  revertCamera()
}

const confirmText = computed(() => {
  const parts = []
  if (enablingScreen()) parts.push('开启屏幕感知后会读取前台窗口标题，并在需要时截图交给识图模型')
  if (enablingProactive()) parts.push('开启主动对话后，桌宠会在你空闲、回到电脑前、到饭点或深夜时主动找你说话，每次都会调用一次聊天模型')
  return parts.join('；') + '（会产生费用）。确定开启吗？'
})

function onSave(): void {
  if (enablingScreen() || enablingProactive()) confirmOpen.value = true
  else void doSave()
}

function onConfirm(): void {
  confirmOpen.value = false
  void doSave()
}

async function lookLater(): Promise<void> {
  looking.value = true
  try {
    const r = await lookOnce(5)
    if (r.scheduled) {
      message.info('5 秒后截取当时的前台窗口，请切换到想让它看的窗口')
      countdown.value = 5
      if (countdownTimer) clearInterval(countdownTimer)
      countdownTimer = setInterval(() => {
        countdown.value -= 1
        if (countdown.value <= 0 && countdownTimer) {
          clearInterval(countdownTimer)
          countdownTimer = null
          countdown.value = 0
        }
      }, 1000)
    } else {
      message.error(r.error ?? '无法安排截图', { duration: 6000 })
    }
  } catch (e) {
    message.error(errorText(e), { duration: 6000 })
  } finally {
    looking.value = false
  }
}

async function runWeather(): Promise<void> {
  if (cityDirty()) {
    message.warning('请先保存')
    return
  }
  weatherBusy.value = true
  weatherText.value = null
  weatherError.value = null
  try {
    const r = await queryWeather()
    if (r.text) weatherText.value = r.text
    else weatherError.value = r.error ?? '查询失败'
  } catch (e) {
    weatherError.value = errorText(e)
  } finally {
    weatherBusy.value = false
  }
}
</script>

<template>
  <page-head title="感知与主动" />
  <n-spin :show="s.loading && !s.loaded">
    <backend-offline v-if="s.offline || (!s.loaded && s.error)" :error="s.error" :loading="s.loading" @retry="loadScreen" />
    <n-space v-else-if="s.loaded" vertical :size="20">
      <n-card title="看屏幕吐槽">
        <n-space vertical :size="14">
          <n-space align="center">
            <n-switch v-model:value="s.screen.enabled" />
            <span>开启屏幕感知</span>
          </n-space>
          <span class="hint">开启后桌宠会读取前台窗口的标题；切到新的游戏 / 视频 / 网页并停留一会儿后，截一张当前窗口的图给识图模型，决定要不要说两句。截图只在内存里，用完即丢，不保存。</span>
          <n-alert v-if="s.status.vision_profile === null" type="warning" :closable="false">
            还没有指定识图用的 LLM，看屏幕不会生效。请到「识图」页设置。
          </n-alert>

          <n-radio-group v-model:value="s.screen.mode">
            <n-space vertical :size="10">
              <div v-for="m in modes" :key="m.value">
                <n-radio :value="m.value">{{ m.label }}</n-radio>
                <div class="hint sub">{{ cadenceText(s.cadences[m.value]) }}</div>
              </div>
            </n-space>
          </n-radio-group>
          <span class="hint">适中和话痨模式下，同一个画面过了冷却期、又变化很大时也会再看。每次「看一眼」都会调用一次识图模型（不论最后说没说话都算一次），这是主要的费用来源。</span>

          <n-space align="center">
            <n-switch v-model:value="s.screen.allow_fullscreen" />
            <span>全屏时也吐槽（游戏、视频）</span>
          </n-space>
          <span class="hint">会议、录屏、直播永远不打扰，名单见下方「免打扰」。</span>

          <n-space align="center">
            <span>主人说话后多久不插嘴（秒）</span>
            <n-input-number v-model:value="s.screen.quiet_after_user_seconds" :min="0" :max="600" :step="5" :precision="0" style="width: 130px" />
          </n-space>

          <n-collapse>
            <n-collapse-item title="高级" name="adv">
              <n-space align="center">
                <span>读取前台窗口的间隔（秒）</span>
                <n-input-number v-model:value="s.screen.poll_seconds" :min="0.5" :max="30" :step="0.5" style="width: 130px" />
              </n-space>
            </n-collapse-item>
          </n-collapse>
        </n-space>
      </n-card>

      <n-card title="主动对话">
        <n-space vertical :size="14">
          <n-space align="center">
            <n-switch v-model:value="s.proactive.enabled" />
            <span>开启主动对话</span>
          </n-space>
          <span class="hint">开启后桌宠会在三种情况下自己找你说话：你在电脑前但很久没理它（空闲搭话）、你离开一会儿再回来（欢迎回来）、到了饭点或深夜（每天最多一次）。只看时间、天气和前台窗口标题，不截图。和「看屏幕吐槽」共用每小时的主动说话次数。挂机钓鱼时桌宠自己说的台词也跟随这个开关：关着就只显示小窗动画，不说话。</span>

          <n-radio-group v-model:value="s.proactive.mode">
            <n-space vertical :size="10">
              <div v-for="m in modes" :key="m.value">
                <n-radio :value="m.value">{{ m.label }}</n-radio>
                <div class="hint sub">{{ chatCadenceText(s.chatCadences[m.value]) }}</div>
              </div>
            </n-space>
          </n-radio-group>

          <n-space align="center">
            <n-switch v-model:value="s.proactive.idle_chat" />
            <span>空闲搭话</span>
            <n-input-number v-model:value="s.proactive.idle_after_minutes" :min="3" :max="240" :step="1" :precision="0" :disabled="!s.proactive.idle_chat" style="width: 130px" />
            <span>分钟没说话后</span>
          </n-space>
          <n-space align="center">
            <n-switch v-model:value="s.proactive.welcome_back" />
            <span>离开超过</span>
            <n-input-number v-model:value="s.proactive.away_minutes" :min="3" :max="240" :step="1" :precision="0" :disabled="!s.proactive.welcome_back" style="width: 130px" />
            <span>分钟再回来时打招呼</span>
          </n-space>
          <n-space align="center">
            <n-switch v-model:value="s.proactive.greetings" />
            <span>时间段问候（早安、午饭、晚饭、深夜催睡，每种每天一次）</span>
          </n-space>
        </n-space>
      </n-card>

      <n-card title="空闲动作">
        <n-space vertical :size="14">
          <n-space align="center">
            <n-switch v-model:value="s.proactive.idle_action" />
            <span>超过</span>
            <n-input-number v-model:value="s.proactive.idle_action_minutes" :min="1" :max="240" :step="1" :precision="0" :disabled="!s.proactive.idle_action" style="width: 130px" />
            <span>分钟没和桌宠互动时，自动做一个动作</span>
          </n-space>
          <span class="hint">从你最后一次和桌宠互动（打字、说话、点它、快捷键）算起，不看鼠标键盘有没有动。到时间从下面选中的标签里各随机挑一个来做；之后每隔同样的时间再做一次，直到你再次互动。不调用模型、不说话。当前模型没有的标签会自动跳过。想让动作一直保持到你下次互动，在「形象」页给那个动作勾上「循环」。</span>
          <n-space align="center" :wrap="false">
            <span>动作</span>
            <n-select v-model:value="s.proactive.idle_action_motions" multiple :options="motionOptions" placeholder="选择动作标签" :disabled="!s.proactive.idle_action" style="min-width: 320px" />
          </n-space>
          <n-space align="center" :wrap="false">
            <span>表情</span>
            <n-select v-model:value="s.proactive.idle_action_expressions" multiple :options="expressionOptions" placeholder="选择表情标签" :disabled="!s.proactive.idle_action" style="min-width: 320px" />
          </n-space>
          <span v-if="s.labels.motions.length === 0 && s.labels.expressions.length === 0" class="hint">当前模型还没有表情/动作标签（或桌宠未连接），请先在「形象」页设置。</span>
        </n-space>
      </n-card>

      <n-card title="当前状态">
        <n-space vertical :size="12">
          <n-space align="center" :size="8">
            <span class="label">前台窗口</span>
            <span v-if="s.status.foreground === null" class="hint">（还没有记录，屏幕感知关闭时不读取）</span>
            <template v-else>
              <span>{{ s.status.foreground.app }}</span>
              <span class="hint">{{ s.status.foreground.title }}</span>
              <n-tag v-if="s.status.foreground.fullscreen" size="small">全屏</n-tag>
              <n-tag v-if="s.status.foreground.blocked" type="error" size="small">隐私窗口，不截图</n-tag>
              <n-tag v-if="s.status.foreground.quiet" type="warning" size="small">免打扰</n-tag>
            </template>
          </n-space>
          <n-space v-if="!s.status.pet_connected" align="center" :size="8">
            <span class="label">桌宠</span>
            <n-tag type="warning" size="small">桌宠未连接，不会主动说话</n-tag>
          </n-space>
          <n-space align="center" :size="8">
            <span class="label">本小时</span>
            <span>看了 {{ s.status.looks_last_hour }} 次 · 说了 {{ s.status.spoken_last_hour }} 次</span>
          </n-space>
          <n-space align="center" :size="8">
            <span class="label">系统空闲</span>
            <span v-if="s.status.system_idle_seconds === null" class="hint">读不到</span>
            <span v-else>{{ Math.floor(s.status.system_idle_seconds / 60) }} 分 {{ Math.floor(s.status.system_idle_seconds % 60) }} 秒</span>
          </n-space>
          <n-space align="center" :size="8">
            <span class="label">上一次主动搭话</span>
            <span v-if="s.status.last_proactive_at === null" class="hint">还没有</span>
            <span v-else>{{ formatTime(s.status.last_proactive_at) }} · {{ { idle: '空闲搭话', welcome_back: '欢迎回来', greeting: '时间问候' }[s.status.last_proactive_kind ?? ''] ?? '' }}：{{ s.status.last_proactive_text ?? '' }}</span>
          </n-space>
          <n-space align="center" :size="8">
            <span class="label">上一次吐槽</span>
            <span v-if="s.status.last_comment_at === null" class="hint">还没有</span>
            <span v-else>{{ formatTime(s.status.last_comment_at) }} · {{ s.status.last_comment_app ?? '' }}：{{ s.status.last_comment_text ?? '' }}</span>
          </n-space>
          <n-space align="center">
            <n-button :loading="looking" :disabled="countdown > 0" @click="lookLater">
              {{ countdown > 0 ? `${countdown} 秒…` : '5 秒后看一眼' }}
            </n-button>
            <span class="hint">测试用，会跳过频率限制，但不会绕过隐私黑名单。</span>
          </n-space>
        </n-space>
      </n-card>

      <n-card title="天气">
        <n-space vertical :size="14">
          <n-space align="center">
            <span>城市</span>
            <n-input v-model:value="s.environment.city" :maxlength="40" clearable placeholder="例如 杭州、北京 朝阳" style="width: 260px" />
          </n-space>
          <span class="hint">每次回复都会附带当前时间和这座城市的天气（每 30 分钟更新一次，数据来自 Open-Meteo，不用注册，也不按 IP 定位）。</span>
          <n-space align="center">
            <n-switch v-model:value="s.environment.weather" />
            <span>附带天气</span>
          </n-space>
          <n-button style="align-self: flex-start" :loading="weatherBusy" @click="runWeather">查询一次</n-button>
          <span v-if="weatherText" class="ok">{{ weatherText }}</span>
          <span v-if="weatherError" class="error">{{ weatherError }}</span>
        </n-space>
      </n-card>

      <n-card title="隐私">
        <n-space vertical :size="16">
          <div>
            <div class="label-row">黑名单 · 进程名</div>
            <n-dynamic-tags v-model:value="s.privacy.block_processes" :max="500" :input-props="{ maxlength: 100 }" />
            <div class="hint">例如 chrome.exe（在任务管理器「详细信息」里能看到）。这些程序的窗口绝不截图、不评论。</div>
          </div>
          <div>
            <div class="label-row">黑名单 · 标题关键词</div>
            <n-dynamic-tags v-model:value="s.privacy.block_titles" :max="500" :input-props="{ maxlength: 100 }" />
            <div class="hint">窗口标题包含这些词就不截图，不区分大小写。默认已包含网银、支付、密码、无痕浏览等。</div>
          </div>
          <div>
            <div class="label-row">免打扰 · 进程名</div>
            <n-dynamic-tags v-model:value="s.privacy.quiet_processes" :max="500" :input-props="{ maxlength: 100 }" />
            <div class="hint">这些程序在前台时桌宠不主动说话（会议、录屏、直播）。</div>
          </div>
          <div>
            <div class="label-row">免打扰 · 标题关键词</div>
            <n-dynamic-tags v-model:value="s.privacy.quiet_titles" :max="500" :input-props="{ maxlength: 100 }" />
          </div>
        </n-space>
      </n-card>

      <camera-card />
    </n-space>
  </n-spin>
  <save-bar v-if="s.loaded && !s.offline" :dirty="pageDirty" :saving="saving" @save="onSave" @revert="revertAll" />

  <n-modal
    v-model:show="confirmOpen"
    preset="dialog"
    type="warning"
    title="开启感知功能"
    :content="confirmText"
    positive-text="确定开启"
    negative-text="取消"
    @positive-click="onConfirm"
    @negative-click="confirmOpen = false"
  />
</template>

<style scoped>
.hint.sub {
  margin: 2px 0 0 24px;
}
.label {
  min-width: 9em;
  opacity: 0.8;
}
.label-row {
  margin-bottom: 6px;
}
</style>
