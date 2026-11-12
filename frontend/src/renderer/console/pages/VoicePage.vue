<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  NAlert,
  NButton,
  NCard,
  NCode,
  NCollapse,
  NCollapseItem,
  NDynamicTags,
  NEmpty,
  NForm,
  NFormItem,
  NInput,
  NInputNumber,
  NSelect,
  NSlider,
  NSpace,
  NSpin,
  NSwitch,
  NTable,
  NTag,
  useMessage,
  type SelectOption
} from 'naive-ui'
import BackendOffline from '../components/BackendOffline.vue'
import PageHead from '../components/PageHead.vue'
import SaveBar from '../components/SaveBar.vue'
import { errorText } from '../store'
import { useTask } from '../task'
import { clockTime } from '../util'
import {
  deleteKey,
  dirty,
  groupVoices,
  loadHeard,
  loadVoice,
  loadVoices,
  recheckModels,
  revertVoice,
  saveKey,
  saveListen,
  saveVoice,
  testVoice,
  voice,
  withEmotion
} from '../voice'

const message = useMessage()
const { busy: saving, run: runSave } = useTask()
const listenBusy = ref(false)
const { busy: keyBusy, run: runKey } = useTask()
const { busy: rechecking, run: runRecheck } = useTask()
const labels = ref<string[]>([])

const testText = ref('')
const testExpression = ref<string | null>(null)
const testing = ref(false)
const testOk = ref<string | null>(null)
const testError = ref<string | null>(null)

const modelOptions = computed(() => voice.modelChoices.map((m) => ({ label: m, value: m })))
const voiceOptions = computed(() => groupVoices(voice.voices) as unknown as SelectOption[])
const labelOptions = computed(() => labels.value.map((l) => ({ label: l, value: l })))
const emotionOptions = computed(
  () =>
    [{ label: '自动', value: null }, ...voice.emotionChoices.map((e) => ({ label: e, value: e }))] as unknown as SelectOption[]
)
const asrLanguageOptions: SelectOption[] = [
  { label: '中文（推荐）', value: 'zh' },
  { label: '自动判断', value: 'auto' },
  { label: '英语', value: 'en' },
  { label: '日语', value: 'ja' },
  { label: '韩语', value: 'ko' },
  { label: '粤语', value: 'yue' }
]
const speechLanguageOptions: SelectOption[] = [
  { label: '和气泡相同', value: 'same' },
  { label: '日语（气泡仍显示中文）', value: 'ja' },
  { label: '英语（气泡仍显示中文）', value: 'en' }
]
const providerOptions: SelectOption[] = [
  { label: 'MiniMax', value: 'minimax' },
  { label: '阿里云 CosyVoice（百炼，华北2）', value: 'cosyvoice' },
  { label: '通用接口（OpenAI 兼容 /audio/speech）', value: 'openai' }
]
const KEY_NAMES = { minimax: 'MiniMax', cosyvoice: '阿里云百炼', openai: '语音服务' } as const
function onProvider(): void {
  voice.newKey = ''
}
const provider = computed(() => voice.tts.provider)
const mm = computed(() => voice.tts.minimax)
const cosy = computed(() => voice.tts.cosyvoice)
const oai = computed(() => voice.tts.openai)
const curKey = computed(() => voice.keys[voice.tts.provider])
const keyPlaceholder = computed(() => (curKey.value.set ? '输入新 Key 以替换' : `输入${KEY_NAMES[voice.tts.provider]} API Key`))
const cosyModelOptions = computed(() => voice.cosyModelChoices.map((m) => ({ label: m, value: m })))
// Suggestions for cosyvoice-v3-flash; any voice of the chosen model (or a cloned one) may be typed.
const cosyVoiceOptions: SelectOption[] = [
  { label: '龙安欢 · 欢脱元气女（支持语气）', value: 'longanhuan' },
  { label: '龙呼呼 · 天真女童（支持语气）', value: 'longhuhu_v3' },
  { label: '龙安洋 · 阳光男孩（支持语气）', value: 'longanyang' },
  { label: '龙华 · 元气甜美女', value: 'longhua_v3' },
  { label: '龙菲菲 · 甜美娇气女', value: 'longfeifei_v3' },
  { label: '龙星 · 温婉邻家女', value: 'longxing_v3' },
  { label: '龙安柔 · 温柔闺蜜女', value: 'longanrou_v3' },
  { label: '龙仙 · 豪放可爱女', value: 'longxian_v3' },
  { label: 'Riko · 二次元日语女', value: 'loongriko_v3' },
  { label: 'Yuuna · 日语女', value: 'loongyuuna_v3' }
]
const formatOptions: SelectOption[] = [
  { label: 'pcm（裸 PCM）', value: 'pcm' },
  { label: 'wav', value: 'wav' }
]
const EXTRA_HINT = '随请求一起发送的额外字段（JSON 对象），例如某些服务用 {"sample_rate": 24000} 指定采样率。'
const extraText = ref<string | null>(null)
const extraError = ref<string | null>(null)
const extraShown = computed(() => extraText.value ?? (Object.keys(voice.tts.openai.extra).length ? JSON.stringify(voice.tts.openai.extra) : ''))
function onExtra(v: string): void {
  extraText.value = v
  if (!v.trim()) {
    voice.tts.openai.extra = {}
    extraError.value = null
    return
  }
  try {
    const parsed: unknown = JSON.parse(v)
    if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error()
    voice.tts.openai.extra = parsed as Record<string, unknown>
    extraError.value = null
  } catch {
    extraError.value = '需要是一个 JSON 对象，例如 {"sample_rate": 24000}'
  }
}
const showSaveBar = computed(() => voice.loaded && !voice.offline)
const saveProblem = computed<string | null>(() => (extraError.value ? `附加参数${extraError.value}` : null))
const voiceMismatch = computed<string | null>(() => {
  if (voice.tts.provider !== 'minimax') return null
  const id = voice.tts.minimax.voice_id ?? ''
  if (voice.tts.speech_language === 'ja' && !id.startsWith('Japanese_')) return '日语'
  if (voice.tts.speech_language === 'en' && !id.startsWith('English_')) return '英语'
  return null
})
const extraLabels =computed(() => Object.keys(voice.tts.emotions).filter((k) => !labels.value.includes(k)))

let heardTimer: ReturnType<typeof setInterval> | null = null

onBeforeUnmount(() => {
  if (heardTimer !== null) clearInterval(heardTimer)
  heardTimer = null
})

onMounted(async () => {
  void loadHeard()
  heardTimer = setInterval(() => void loadHeard(), 3000)
  if (!voice.loaded) void loadVoice()
  else if (voice.keys.minimax.set && voice.voices.length === 0) void loadVoices()
  try {
    const actions = await window.petConsole.modelActions()
    labels.value = Object.keys(actions.map.expressions)
  } catch {
    labels.value = []
  }
})

async function onListen(v: boolean): Promise<void> {
  listenBusy.value = true
  try {
    await saveListen(v)
  } catch (e) {
    voice.asr.listen = !v
    message.error(errorText(e), { duration: 6000 })
  } finally {
    listenBusy.value = false
  }
}

const recheck = (): Promise<boolean> => runRecheck(recheckModels)
const onSaveKey = (): Promise<boolean> => runKey(saveKey, 'Key 已保存')
const onDeleteKey = (): Promise<boolean> => runKey(deleteKey, 'Key 已删除')

function emotionOf(label: string): string | null {
  return voice.tts.emotions[label] ?? null
}

function setEmotion(label: string, v: string | null): void {
  voice.tts.emotions = withEmotion(voice.tts.emotions, label, v)
}

const save = (): Promise<boolean> => runSave(saveVoice, '已保存')

async function runTest(): Promise<void> {
  testing.value = true
  testOk.value = null
  testError.value = null
  try {
    const r = await testVoice(testText.value, testExpression.value)
    if (r.ok) testOk.value = r.first_audio_ms !== null ? `试听完成，${(r.first_audio_ms / 1000).toFixed(1)} 秒后开始出声` : '试听完成'
    else testError.value = r.error ?? '试听失败'
  } catch (e) {
    testError.value = errorText(e)
  } finally {
    testing.value = false
  }
}
</script>

<template>
  <page-head title="语音" />
  <n-spin :show="voice.loading && !voice.loaded">
    <backend-offline v-if="voice.offline || (!voice.loaded && voice.error)" :error="voice.error" :loading="voice.loading" @retry="loadVoice" />
    <n-space v-else-if="voice.loaded" vertical :size="20">
      <n-card title="收音">
        <n-space vertical :size="14">
          <n-alert v-if="!voice.modelsReady" type="warning" title="语音识别模型未下载">
            <p class="line">在项目根目录打开终端，执行：</p>
            <n-code :code="`cd backend && ${voice.fetchCommand}`" language="bash" />
            <div class="actions">
              <n-button size="small" :loading="rechecking" @click="recheck">重新检测</n-button>
            </div>
          </n-alert>
          <n-space align="center">
            <n-switch :value="voice.asr.listen" :disabled="!voice.modelsReady || listenBusy" :loading="listenBusy" @update:value="onListen" />
            <span>麦克风常开</span>
          </n-space>
          <span class="hint">开启后一直收音，开口说话会自动识别；关闭后桌宠不会打开麦克风。</span>
          <span class="hint">不想常开？在『快捷键』页启用内置动作『按住说话』：按住快捷键说话，松开立刻识别并发送，麦克风常开关着也能用。</span>
          <n-space align="center">
            <n-switch v-model:value="voice.asr.barge_in" />
            <span>说话时打断桌宠</span>
          </n-space>
          <span class="hint">开启：桌宠说话时你一开口，它就停下来听你说。关闭：它会把当前这句说完，你接着说的话照样会识别，回复排队依次朗读。</span>
          <n-form label-placement="left" label-width="150">
            <n-form-item label="识别语言" feedback="自动判断时，呼吸声、短噪音偶尔会被识别成日语或韩语的单个音节；平时说中文就选中文。">
              <n-select v-model:value="voice.asr.language" :options="asrLanguageOptions" style="width: 200px" />
            </n-form-item>
            <n-form-item label="停顿判定（秒）" feedback="停顿多久算说完。说话容易卡一下就被截断的话调大（1.0~1.5）；越大，说完后桌宠反应越慢。">
              <n-space align="center" :wrap="false">
                <n-slider v-model:value="voice.asr.min_silence_seconds" :min="0.2" :max="3" :step="0.1" style="width: 200px" />
                <n-input-number v-model:value="voice.asr.min_silence_seconds" :min="0.2" :max="3" :step="0.1" style="width: 110px" />
              </n-space>
            </n-form-item>
            <n-form-item label="人声判定门槛" feedback="越高越不容易被噪音触发，但小声说话可能听不到；听不清就调低（0.3~0.4）。">
              <n-space align="center" :wrap="false">
                <n-slider v-model:value="voice.asr.vad_threshold" :min="0.1" :max="0.95" :step="0.05" style="width: 200px" />
                <n-input-number v-model:value="voice.asr.vad_threshold" :min="0.1" :max="0.95" :step="0.05" style="width: 110px" />
              </n-space>
            </n-form-item>
          </n-form>
          <n-collapse>
            <n-collapse-item title="识别参数（高级）" name="asr">
              <n-form label-placement="left" label-width="150">
                <n-form-item label="打断时长（秒）" feedback="开启打断时，要连续说多久才算打断">
                  <n-space align="center" :wrap="false">
                    <n-slider v-model:value="voice.asr.barge_in_seconds" :disabled="!voice.asr.barge_in" :min="0" :max="3" :step="0.1" style="width: 200px" />
                    <n-input-number v-model:value="voice.asr.barge_in_seconds" :disabled="!voice.asr.barge_in" :min="0" :max="3" :step="0.1" style="width: 110px" />
                  </n-space>
                </n-form-item>
                <n-form-item label="单句最长（秒）" feedback="一直说不停时，到这个时长会先切一段发出去">
                  <n-space align="center" :wrap="false">
                    <n-slider v-model:value="voice.asr.max_speech_seconds" :min="5" :max="60" :step="1" style="width: 200px" />
                    <n-input-number v-model:value="voice.asr.max_speech_seconds" :min="5" :max="60" :step="1" style="width: 110px" />
                  </n-space>
                </n-form-item>
                <n-form-item label="线程数" feedback="识别时用几个 CPU 线程，一般不用改。">
                  <n-input-number v-model:value="voice.asr.threads" :min="1" :max="16" :step="1" :precision="0" style="width: 110px" />
                </n-form-item>
              </n-form>
            </n-collapse-item>
          </n-collapse>
        </n-space>
      </n-card>

      <n-card title="不识别的条件">
        <n-space vertical :size="14">
          <span class="hint">符合下面任一条的识别结果会被丢弃，不发给桌宠。按住说话录的内容不受这些限制。</span>
          <n-form label-placement="left" label-width="150">
            <n-form-item label="最少字数" feedback="去掉标点后少于这个字数就丢弃">
              <n-input-number v-model:value="voice.asr.min_chars" :min="1" :max="10" :step="1" :precision="0" style="width: 110px" />
            </n-form-item>
            <n-form-item label="丢弃纯语气词" feedback="只有 嗯、啊、哦 这类字的结果">
              <n-switch v-model:value="voice.asr.drop_fillers" />
            </n-form-item>
            <n-form-item label="短句字数" feedback="不超过这个字数的算短句，配合下一项使用">
              <n-input-number v-model:value="voice.asr.short_chars" :min="1" :max="20" :step="1" :precision="0" style="width: 110px" />
            </n-form-item>
            <n-form-item label="短句最短录音（秒）" feedback="短句的录音短于这个时长就当杂音丢掉；0 = 不检查">
              <n-space align="center" :wrap="false">
                <n-slider v-model:value="voice.asr.short_min_seconds" :min="0" :max="2" :step="0.05" style="width: 200px" />
                <n-input-number v-model:value="voice.asr.short_min_seconds" :min="0" :max="2" :step="0.05" style="width: 110px" />
              </n-space>
            </n-form-item>
            <n-form-item label="忽略词表" feedback="识别结果正好是这些词（不计标点）就丢弃，例如总被噪音识别出来的词">
              <n-dynamic-tags v-model:value="voice.asr.ignore_phrases" :max="50" />
            </n-form-item>
            <n-form-item label="最短人声（秒）" feedback="比这更短的声音不算说话（敲键盘、咳一声）">
              <n-space align="center" :wrap="false">
                <n-slider v-model:value="voice.asr.min_speech_seconds" :min="0.1" :max="1" :step="0.05" style="width: 200px" />
                <n-input-number v-model:value="voice.asr.min_speech_seconds" :min="0.1" :max="1" :step="0.05" style="width: 110px" />
              </n-space>
            </n-form-item>
          </n-form>
        </n-space>
      </n-card>

      <n-card title="最近识别">
        <template #header-extra>
          <n-button size="small" @click="loadHeard">刷新</n-button>
        </template>
        <n-space vertical :size="12">
          <span class="hint">麦克风听到的每一段话和处理结果（只保存在内存里，重启后端清空）。听不清或被截断时，看这里是哪一步出了问题。</span>
          <span v-if="voice.heardError" class="error">{{ voice.heardError }}</span>
          <n-empty v-if="voice.heard.length === 0" description="还没有识别到语音" />
          <div v-else style="max-height: 320px; overflow: auto">
            <n-table size="small" :single-line="false">
              <thead>
                <tr>
                  <th>时间</th>
                  <th>内容</th>
                  <th>时长</th>
                  <th>方式</th>
                  <th>结果</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(h, i) in voice.heard" :key="`${h.ts}-${i}`">
                  <td>{{ clockTime(h.ts) }}</td>
                  <td>{{ h.text || '（无）' }}</td>
                  <td>{{ `${h.seconds.toFixed(1)} 秒` }}</td>
                  <td>{{ h.manual ? '按住说话' : '常开' }}</td>
                  <td>
                    <n-tag v-if="h.sent" type="success" size="small" :bordered="false">已发送</n-tag>
                    <template v-else>
                      <n-tag size="small" :bordered="false">丢弃</n-tag>
                      <span class="hint"> {{ h.reason }}</span>
                    </template>
                  </td>
                </tr>
              </tbody>
            </n-table>
          </div>
        </n-space>
      </n-card>

      <n-card title="语音合成">
        <n-form label-placement="left" label-width="150">
          <n-form-item label="朗读回复">
            <n-switch v-model:value="voice.tts.enabled" />
          </n-form-item>
          <n-form-item label="服务商" feedback="每个服务商的设置和 API Key 分开保存，切换后不会丢。切换后要点「保存」才生效。">
            <n-select v-model:value="voice.tts.provider" :options="providerOptions" style="width: 320px" @update:value="onProvider" />
          </n-form-item>
          <n-form-item label="API Key">
            <n-space vertical :size="8" style="width: 100%">
              <n-space v-if="curKey.set" align="center">
                <span>已保存 {{ curKey.hint }}</span>
                <n-button size="small" :loading="keyBusy" @click="onDeleteKey">删除</n-button>
              </n-space>
              <n-space :wrap="false">
                <n-input
                  v-model:value="voice.newKey"
                  type="password"
                  show-password-on="click"
                  autocomplete="off"
                  :placeholder="keyPlaceholder"
                  style="width: 320px"
                />
                <n-button :disabled="!voice.newKey.trim()" :loading="keyBusy" @click="onSaveKey">保存 Key</n-button>
              </n-space>
              <span v-if="provider === 'cosyvoice'" class="hint">在阿里云百炼控制台（华北2 北京）创建的 API Key，sk- 开头。</span>
              <span v-if="provider === 'openai'" class="hint">本机服务（http://127.0.0.1）可以不填 Key。</span>
            </n-space>
          </n-form-item>

          <template v-if="provider === 'minimax'">
            <n-form-item label="语音模型">
              <n-select v-model:value="voice.tts.minimax.model" :options="modelOptions" style="width: 280px" />
            </n-form-item>
            <n-form-item label="音色">
              <n-space vertical :size="6" style="width: 100%">
                <n-space :wrap="false">
                  <n-select
                    v-model:value="voice.tts.minimax.voice_id"
                    :options="voiceOptions"
                    filterable
                    tag
                    placeholder="选择或输入音色 ID"
                    style="width: 320px"
                  />
                  <n-button :loading="voice.voicesLoading" @click="loadVoices">刷新音色列表</n-button>
                </n-space>
                <span v-if="voice.voicesError" class="error">{{ voice.voicesError }}</span>
              </n-space>
            </n-form-item>
          </template>

          <template v-else-if="provider === 'cosyvoice'">
            <n-form-item label="语音模型" feedback="下面列表里的系统音色都属于 cosyvoice-v3-flash，请选它。音色和模型对不上时会朗读失败；v3.5 等模型只能用为它创建的复刻/设计音色。">
              <n-select v-model:value="voice.tts.cosyvoice.model" :options="cosyModelOptions" filterable tag style="width: 280px" />
            </n-form-item>
            <n-form-item label="音色" feedback="列表是 cosyvoice-v3-flash 的常用音色；可直接输入百炼音色列表里的其他音色名或复刻音色 ID。音色必须属于所选模型，否则会报参数错误。">
              <n-select v-model:value="voice.tts.cosyvoice.voice" :options="cosyVoiceOptions" filterable tag placeholder="选择或输入音色" style="width: 320px" />
            </n-form-item>
            <n-form-item label="带语气" feedback="把下面「表情 → 语气」里对应的语气发给语音模型。只有标了「支持语气」的音色可用；其他音色不支持时会自动改回默认语气。">
              <n-switch v-model:value="voice.tts.cosyvoice.emotion_instruct" />
            </n-form-item>
          </template>

          <template v-else>
            <n-form-item label="接口地址" feedback="填到 /v1 为止，程序会请求 接口地址/audio/speech。改了地址要先保存再试听。">
              <n-input v-model:value="voice.tts.openai.base_url" placeholder="https://api.openai.com/v1" style="width: 320px" />
            </n-form-item>
            <n-form-item label="语音模型">
              <n-input v-model:value="voice.tts.openai.model" placeholder="gpt-4o-mini-tts" style="width: 280px" />
            </n-form-item>
            <n-form-item label="音色">
              <n-input v-model:value="voice.tts.openai.voice" placeholder="alloy" style="width: 280px" />
            </n-form-item>
            <n-form-item label="音频格式" feedback="服务必须返回 16 位单声道的 pcm 或 wav（不支持 mp3）。">
              <n-select v-model:value="voice.tts.openai.response_format" :options="formatOptions" style="width: 200px" />
            </n-form-item>
            <n-form-item label="采样率" feedback="服务实际返回的采样率（OpenAI 的 pcm 是 24000）。声音变快变尖或变慢变沉，就是这里填错了。">
              <n-input-number v-model:value="voice.tts.openai.sample_rate" :min="8000" :max="48000" :step="1000" :precision="0" style="width: 140px" />
            </n-form-item>
          </template>

          <n-form-item label="朗读语言" feedback="选日语或英语时，桌宠会把每句话同时写成中文（气泡）和对应语言（朗读），请搭配同语言的音色。">
            <n-space vertical :size="8" style="width: 100%">
              <n-select v-model:value="voice.tts.speech_language" :options="speechLanguageOptions" style="width: 280px" />
              <n-alert v-if="voiceMismatch" type="info" :closable="false">当前音色可能不是{{ voiceMismatch }}音色，读出来会带口音。</n-alert>
            </n-space>
          </n-form-item>

          <template v-if="provider === 'minimax'">
            <n-form-item label="语速">
              <n-space align="center" :wrap="false">
                <n-slider v-model:value="voice.tts.minimax.speed" :min="0.5" :max="2" :step="0.05" style="width: 200px" />
                <n-input-number v-model:value="voice.tts.minimax.speed" :min="0.5" :max="2" :step="0.05" style="width: 110px" />
              </n-space>
            </n-form-item>
            <n-form-item label="音量">
              <n-space align="center" :wrap="false">
                <n-slider v-model:value="voice.tts.minimax.vol" :min="0.1" :max="10" :step="0.1" style="width: 200px" />
                <n-input-number v-model:value="voice.tts.minimax.vol" :min="0.1" :max="10" :step="0.1" style="width: 110px" />
              </n-space>
            </n-form-item>
            <n-form-item label="音调" :show-feedback="false">
              <n-space align="center" :wrap="false">
                <n-slider v-model:value="voice.tts.minimax.pitch" :min="-12" :max="12" :step="1" style="width: 200px" />
                <n-input-number v-model:value="voice.tts.minimax.pitch" :min="-12" :max="12" :step="1" :precision="0" style="width: 110px" />
              </n-space>
            </n-form-item>
          </template>
          <template v-else-if="provider === 'cosyvoice'">
            <n-form-item label="语速">
              <n-space align="center" :wrap="false">
                <n-slider v-model:value="voice.tts.cosyvoice.rate" :min="0.5" :max="2" :step="0.05" style="width: 200px" />
                <n-input-number v-model:value="voice.tts.cosyvoice.rate" :min="0.5" :max="2" :step="0.05" style="width: 110px" />
              </n-space>
            </n-form-item>
            <n-form-item label="音量">
              <n-space align="center" :wrap="false">
                <n-slider v-model:value="voice.tts.cosyvoice.volume" :min="0" :max="100" :step="1" style="width: 200px" />
                <n-input-number v-model:value="voice.tts.cosyvoice.volume" :min="0" :max="100" :step="1" :precision="0" style="width: 110px" />
              </n-space>
            </n-form-item>
            <n-form-item label="音调" :show-feedback="false">
              <n-space align="center" :wrap="false">
                <n-slider v-model:value="voice.tts.cosyvoice.pitch" :min="0.5" :max="2" :step="0.05" style="width: 200px" />
                <n-input-number v-model:value="voice.tts.cosyvoice.pitch" :min="0.5" :max="2" :step="0.05" style="width: 110px" />
              </n-space>
            </n-form-item>
          </template>
          <template v-else>
            <n-form-item label="语速" :show-feedback="false">
              <n-space align="center" :wrap="false">
                <n-slider v-model:value="voice.tts.openai.speed" :min="0.25" :max="4" :step="0.05" style="width: 200px" />
                <n-input-number v-model:value="voice.tts.openai.speed" :min="0.25" :max="4" :step="0.05" style="width: 110px" />
              </n-space>
            </n-form-item>
          </template>
        </n-form>
        <n-collapse style="margin-top: 12px">
          <n-collapse-item title="高级" name="tts-adv">
            <n-form label-placement="left" label-width="150">
            <template v-if="provider === 'minimax'">
              <n-form-item label="接口地址">
                <n-input v-model:value="mm.base_url" placeholder="https://api.minimax.cn" style="width: 320px" />
              </n-form-item>
              <n-form-item label="超时（秒）" :show-feedback="false">
                <n-input-number v-model:value="mm.timeout" :min="1" :max="300" :step="1" style="width: 110px" />
              </n-form-item>
            </template>
            <template v-if="provider === 'cosyvoice'">
              <n-form-item label="接口地址" feedback="默认是华北2（北京）。改了地址要先保存再试听。">
                <n-input v-model:value="cosy.url" placeholder="wss://dashscope.aliyuncs.com/api-ws/v1/inference" style="width: 420px" />
              </n-form-item>
              <n-form-item label="说话风格" feedback="方言或角色要求，必须照百炼文档的格式写（含句号），例如：请用四川话表达。只有支持指令的音色可用；留空 = 不用。">
                <n-input v-model:value="cosy.instruction" :maxlength="200" placeholder="留空" style="width: 420px" />
              </n-form-item>
              <n-form-item label="超时（秒）" :show-feedback="false">
                <n-input-number v-model:value="cosy.timeout" :min="1" :max="300" :step="1" style="width: 110px" />
              </n-form-item>
            </template>
            <template v-if="provider === 'openai'">
              <n-form-item label="附加参数" :feedback="extraError ?? EXTRA_HINT" :validation-status="extraError ? 'error' : undefined">
                <n-input :value="extraShown" placeholder="{}" style="width: 420px" @update:value="onExtra" />
              </n-form-item>
              <n-form-item label="超时（秒）" :show-feedback="false">
                <n-input-number v-model:value="oai.timeout" :min="1" :max="300" :step="1" style="width: 110px" />
              </n-form-item>
            </template>
            </n-form>
          </n-collapse-item>
        </n-collapse>
      </n-card>

      <n-card title="表情 → 语气">
        <n-space vertical :size="12">
          <span class="hint">标签名本身就是语气名（如 happy）时会自动对应，不用设置。</span>
          <span v-if="provider === 'cosyvoice'" class="hint">CosyVoice 只认 happy / sad / angry / fearful / surprised / disgusted / calm，并且要打开上面的「带语气」。</span>
          <span v-if="provider === 'openai'" class="hint">通用接口不支持语气，这里的设置不起作用。</span>
          <n-empty v-if="labels.length === 0" description="当前模型还没有表情标签，请先在「形象」页设置" />
          <n-form v-else label-placement="left" label-width="150">
            <n-form-item v-for="l in labels" :key="l" :label="l" :show-feedback="false" style="margin-bottom: 8px">
              <n-select :value="emotionOf(l)" :options="emotionOptions" style="width: 200px" @update:value="(v: string | null) => setEmotion(l, v)" />
            </n-form-item>
          </n-form>
          <span v-if="extraLabels.length" class="hint">另有当前模型没有的标签映射已保留：{{ extraLabels.join('、') }}</span>
        </n-space>
      </n-card>

      <n-card title="试听">
        <n-space vertical :size="12">
          <n-input v-model:value="testText" type="textarea" :maxlength="200" show-count :autosize="{ minRows: 2, maxRows: 4 }" placeholder="你好呀，我是你的桌宠，以后请多指教！" />
          <n-space align="center">
            <n-select v-model:value="testExpression" :options="labelOptions" clearable placeholder="表情标签（可选）" style="width: 200px" />
            <n-button type="primary" :loading="testing" @click="runTest">试听</n-button>
            <span v-if="testOk" class="ok">{{ testOk }}</span>
            <span v-if="testError" class="error">{{ testError }}</span>
          </n-space>
        </n-space>
      </n-card>
    </n-space>
  </n-spin>
  <save-bar v-if="showSaveBar" :dirty="dirty" :saving="saving" :problem="saveProblem" @save="save" @revert="revertVoice" />
</template>

<style scoped>
.line {
  margin: 0 0 6px;
}
.actions {
  margin-top: 10px;
}
</style>
