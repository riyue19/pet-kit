import * as PIXI from 'pixi.js'
import { install as installUnsafeEval } from '@pixi/unsafe-eval'
import type { Live2DModel as Live2DModelType } from 'pixi-live2d-display/cubism4'
import { placeModel, requiredWindowSize, type Point } from '../shared/geometry'
import type { ModelLayout } from '../shared/ipc'
import type { PetStyle } from '../shared/ui-style'
import type { ActionMap, AgentOutput, LinkStatus, MotionRef, PlayCommand, StatusChange } from '../shared/agent'
import { MicCapture, MouthFollower, SpeechPlayer } from './audio'
import { createFishingHud, type FishingHud } from './fishing-hud'

// Replaces PIXI's new Function() shader codegen so the CSP can keep 'unsafe-eval' disabled.
installUnsafeEval(PIXI)

const HIT_TEST_INTERVAL_MS = 50
const HEAD_Y_RATIO = 0.25
const HEAD_HIT_RATIO = 0.3
const CHAT_HEIGHT_RATIO = 0.2
const CLICK_MAX_MOVE_PX = 5
const CLICK_MAX_MS = 500
// Bubble anchor: its bottom-right corner sits just left of the head, roughly at eye level.
const HEAD_HALF_WIDTH_RATIO = 0.12
const BUBBLE_MARGIN_PX = 8
const BUBBLE_MIN_WIDTH_PX = 120
const THINKING_TEXT = '…'
const HEARD_SHOW_MS = 4000
const IMAGE_MAX_SIDE_PX = 1600
const IMAGE_JPEG_QUALITY = 0.85
const DEFAULT_IMAGE_PROMPT = '帮我看看这张图。有外文就翻译成中文，有文字就读出来。'

const notice = document.getElementById('notice') as HTMLDivElement
const canvas = document.getElementById('stage') as HTMLCanvasElement
const bubble = document.getElementById('bubble') as HTMLDivElement
const chat = document.getElementById('chat') as HTMLFormElement
const chatInput = document.getElementById('chat-input') as HTMLTextAreaElement
const micDot = document.getElementById('mic') as HTMLDivElement
const attach = document.getElementById('attach') as HTMLDivElement
const attachImg = attach.querySelector('img') as HTMLImageElement
const attachRemove = attach.querySelector('button') as HTMLButtonElement
const camDot = document.getElementById('cam') as HTMLDivElement
const statusFx = document.getElementById('status-fx') as HTMLDivElement
const fishingEl = document.getElementById('fishing') as HTMLDivElement

function showNotice(msg: string): void {
  notice.textContent = msg
  notice.style.display = 'block'
  console.warn(msg)
}

const clamp = (v: number, lo: number, hi: number): number => Math.min(hi, Math.max(lo, v))

// Preset ids go to data attributes (matched by pet-style.css); user CSS goes last in <head> so it wins ties.
const userCss = document.createElement('style')
userCss.id = 'user-css'
document.head.appendChild(userCss)
function applyPetStyle(s: PetStyle): void {
  document.documentElement.dataset.inputStyle = s.inputStyle
  document.documentElement.dataset.bubbleStyle = s.bubbleStyle
  userCss.textContent = s.petCss
  // Padding/font changes can resize the bubble; keep it anchored.
  if (bubble.classList.contains('show')) requestAnimationFrame(() => positionBubbleHook())
}
let positionBubbleHook: () => void = () => undefined

async function main(): Promise<void> {
  const cfg = await window.pet.getConfig()
  applyPetStyle(cfg.petStyle)
  window.pet.onPetStyle(applyPetStyle)
  if (!cfg.modelPath) {
    showNotice('尚未配置模型。\n托盘图标右键 →「控制台…」选择或导入模型。\n没有模型可先在 frontend 目录运行 npm run fetch-sample-model。')
    return
  }
  if (!(window as unknown as { Live2DCubismCore?: unknown }).Live2DCubismCore) {
    showNotice('未找到 Cubism Core。\n请在 frontend 目录运行 npm run fetch-core。')
    return
  }

  // Imported dynamically: the module throws at evaluation time if Cubism Core is missing.
  const { Live2DModel, MotionPriority } = await import('pixi-live2d-display/cubism4')
  Live2DModel.registerTicker(PIXI.Ticker)

  const app = new PIXI.Application({
    view: canvas,
    resizeTo: window,
    backgroundAlpha: 0,
    antialias: true,
    autoDensity: true,
    resolution: window.devicePixelRatio,
    // Required so readPixels in the hit test sees the last rendered frame.
    preserveDrawingBuffer: true
  })

  let model: Live2DModelType
  try {
    model = await Live2DModel.from(cfg.modelPath, { autoInteract: false })
  } catch (e) {
    showNotice(`模型加载失败：${cfg.modelPath}\n${e instanceof Error ? e.message : String(e)}\n托盘图标右键 →「控制台…」可重新选择模型。`)
    return
  }
  app.stage.addChild(model)

  const natural = { width: model.internalModel.width, height: model.internalModel.height }
  const clipMask = new PIXI.Graphics()
  app.stage.addChild(clipMask)
  let modelLayout: ModelLayout = { scale: cfg.scale, framing: cfg.framing, halfRatio: cfg.halfRatio }

  // Keeps the chat bubble at the upper-left of the head, clamped inside the window.
  const positionBubble = (): void => {
    const w = natural.width * model.scale.x
    const h = natural.height * model.scale.y
    const headLeft = model.x + w * (0.5 - HEAD_HALF_WIDTH_RATIO)
    const headY = model.y + h * HEAD_Y_RATIO
    const maxRight = window.innerWidth - BUBBLE_MARGIN_PX
    const right = clamp(headLeft, BUBBLE_MARGIN_PX + BUBBLE_MIN_WIDTH_PX, maxRight)
    bubble.style.maxWidth = `${right - BUBBLE_MARGIN_PX}px`
    bubble.style.left = 'auto'
    bubble.style.right = `${window.innerWidth - right}px`
    const height = bubble.offsetHeight
    bubble.style.top = `${clamp(headY - height, BUBBLE_MARGIN_PX, Math.max(BUBBLE_MARGIN_PX, window.innerHeight - height - BUBBLE_MARGIN_PX))}px`
  }

  positionBubbleHook = positionBubble

  const layout = (): void => {
    // The bottom strip belongs to the chat bar, so the half-body cut lands above it.
    const area = { x: 0, y: 0, width: window.innerWidth, height: window.innerHeight * (1 - CHAT_HEIGHT_RATIO) }
    const placed = placeModel(natural, area, modelLayout, 8)
    model.scale.set(placed.scale)
    model.position.set(placed.x, placed.y)
    clipMask.clear()
    if (placed.clip) {
      clipMask.beginFill(0xffffff).drawRect(placed.clip.x, placed.clip.y, placed.clip.width, placed.clip.height).endFill()
      model.mask = clipMask
    } else {
      model.mask = null
    }
    positionBubble()
    // A fixed scale larger than the window would be cropped: ask for a window that holds it.
    window.pet.fitWindow(requiredWindowSize(natural, modelLayout, CHAT_HEIGHT_RATIO, 8))
  }
  layout()
  window.addEventListener('resize', layout)
  window.pet.onLayout((next) => {
    modelLayout = next
    layout()
  })

  // Eye tracking: magnitude scales with distance from the head instead of Live2DModel.focus's
  // always-saturated unit vector, so the gaze relaxes when the cursor is close to the face.
  let cursor: Point | null = null
  const lookAt = (p: Point): void => {
    // Natural size x scale: model.width/height would shrink when the half-body mask clips bounds.
    const w = natural.width * model.scale.x
    const h = natural.height * model.scale.y
    const headX = model.x + w / 2
    const headY = model.y + h * HEAD_Y_RATIO
    const radius = Math.max(w, h)
    const fx = clamp((p.x - headX) / radius, -1, 1)
    const fy = clamp(-(p.y - headY) / radius, -1, 1)
    model.internalModel.focusController.focus(fx, fy)
  }
  window.pet.onCursor((p) => {
    cursor = p
    lookAt(p)
  })

  // Click-through: opaque model pixel under the cursor => accept mouse; otherwise pass through.
  const gl = (app.renderer as PIXI.Renderer).gl
  const pixel = new Uint8Array(4)
  let ignoring = true
  let dragging = false

  const setIgnore = (ignore: boolean): void => {
    if (ignore === ignoring) return
    ignoring = ignore
    window.pet.setIgnoreMouse(ignore)
  }

  const isOverModel = (p: Point): boolean => {
    if (p.x < 0 || p.y < 0 || p.x >= window.innerWidth || p.y >= window.innerHeight) return false
    // Cubism masks render into offscreen framebuffers; make sure we read the screen.
    ;(app.renderer as PIXI.Renderer).renderTexture.bind()
    const dpr = app.renderer.resolution
    const px = Math.floor(p.x * dpr)
    const py = gl.drawingBufferHeight - 1 - Math.floor(p.y * dpr)
    gl.readPixels(px, py, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, pixel)
    return pixel[3] > cfg.alphaThreshold
  }

  const isOverChat = (p: Point): boolean => {
    const inside = (r: DOMRect): boolean => p.x >= r.left && p.x <= r.right && p.y >= r.top && p.y <= r.bottom
    return inside(chatInput.getBoundingClientRect()) || (attach.classList.contains('on') && inside(attach.getBoundingClientRect()))
  }

  // The fishing HUD takes the mouse only during a bite window (a click then lands the fish).
  let fishingHud: FishingHud | null = null
  const isOverFishing = (p: Point): boolean => {
    if (!fishingHud?.clickable()) return false
    const r = fishingEl.getBoundingClientRect()
    return p.x >= r.left && p.x <= r.right && p.y >= r.top && p.y <= r.bottom
  }

  const hitTest = (): void => {
    if (dragging || !cursor) return
    setIgnore(!isOverChat(cursor) && !isOverFishing(cursor) && !isOverModel(cursor))
  }

  // readPixels stalls the GPU, so mousemove (possibly 1000Hz) is coalesced to one test per frame.
  let hitTestQueued = false
  window.addEventListener('mousemove', (e) => {
    cursor = { x: e.clientX, y: e.clientY }
    if (hitTestQueued) return
    hitTestQueued = true
    requestAnimationFrame(() => {
      hitTestQueued = false
      hitTest()
    })
  })
  // Covers the model animating under a still cursor.
  setInterval(hitTest, HIT_TEST_INTERVAL_MS)

  // Moving to a monitor with a different scale factor changes DPR; re-render at the new density.
  const watchDpr = (): void => {
    const mq = matchMedia(`(resolution: ${window.devicePixelRatio}dppx)`)
    mq.addEventListener(
      'change',
      () => {
        app.renderer.resolution = window.devicePixelRatio
        app.renderer.resize(window.innerWidth, window.innerHeight)
        watchDpr()
      },
      { once: true }
    )
  }
  watchDpr()

  // A press that neither moved nor lingered is a click (poke), otherwise it was a drag.
  let press: { x: number; y: number; t: number } | null = null
  canvas.addEventListener('pointerdown', (e) => {
    if (e.button !== 0 || ignoring) return
    dragging = true
    press = { x: e.screenX, y: e.screenY, t: performance.now() }
    canvas.setPointerCapture(e.pointerId)
    window.pet.dragStart()
  })
  const endDrag = (): void => {
    press = null
    if (!dragging) return
    dragging = false
    window.pet.dragEnd()
    hitTest()
  }
  canvas.addEventListener('pointerup', (e) => {
    const p = press
    endDrag()
    if (!p || e.button !== 0) return
    const moved = Math.hypot(e.screenX - p.x, e.screenY - p.y)
    if (moved >= CLICK_MAX_MOVE_PX || performance.now() - p.t >= CLICK_MAX_MS) return
    const headLine = model.y + natural.height * model.scale.y * HEAD_HIT_RATIO
    wake()
    window.pet.interact({ kind: 'click', area: e.clientY < headLine ? 'head' : 'body' })
  })
  canvas.addEventListener('pointercancel', endDrag)
  canvas.addEventListener('lostpointercapture', endDrag)
  canvas.addEventListener('contextmenu', (e) => {
    e.preventDefault()
    if (!ignoring) window.pet.openMenu()
  })
  window.addEventListener('blur', endDrag)

  // --- Actions: label -> model expression / motion ---
  let actions: ActionMap = cfg.actions
  window.pet.onActions((map) => {
    actions = map
  })
  const expressionManager = model.internalModel.motionManager.expressionManager
  let expressionTimer: number | null = null
  // True while the visible expression belongs to the playing motion (held until the motion ends).
  let motionExpression = false
  let playSeq = 0
  const clearExpression = (): void => {
    if (expressionTimer !== null) clearTimeout(expressionTimer)
    expressionTimer = null
    motionExpression = false
    if (!expressionManager) return
    expressionManager.resetExpression()
    // resetExpression leaves currentExpression set, and the library then refuses to show that expression again.
    expressionManager.currentExpression = expressionManager.defaultExpression
  }
  const showExpression = (name: string, holdMs: number | null): void => {
    if (expressionTimer !== null) clearTimeout(expressionTimer)
    expressionTimer = holdMs === null ? null : window.setTimeout(clearExpression, holdMs)
    void model.expression(name)
  }
  // A motion marked `loop` restarts whenever it ends, until the user engages the pet or something else plays.
  let looping: MotionRef | null = null
  const startMotion = (m: MotionRef, seq: number): void => {
    void model.motion(m.group, m.index, MotionPriority.FORCE).then((ok) => {
      if (ok) return
      console.warn(`[actions] 动作 ${m.group} #${m.index + 1} 没有播放（动作文件不存在或加载失败）`)
      if (seq !== playSeq) return
      looping = null
      if (motionExpression) clearExpression()
    })
  }
  const stopMotion = (): void => {
    looping = null
    playSeq++
    // The next frame reports motionFinish, which hides the paired expression and eases the pose back.
    model.internalModel.motionManager.stopAllMotions()
  }
  /** The user engaged the pet (click, typing, talking) or the pet speaks: a held motion ends. */
  const wake = (): void => {
    if (looping) stopMotion()
  }
  const play = (cmd: PlayCommand): void => {
    if (cmd.stop) {
      stopMotion()
      return
    }
    const m = cmd.motion
    if (!m) {
      // A face change must not hide what the running motion needs.
      if (cmd.expression && !motionExpression) showExpression(cmd.expression, actions.holdSeconds * 1000)
      return
    }
    if (looping && looping.group === m.group && looping.index === m.index) return
    const seq = ++playSeq
    looping = m.loop ? m : null
    if (m.expression) {
      showExpression(m.expression, null)
      motionExpression = true
    } else {
      if (motionExpression) clearExpression()
      if (cmd.expression) showExpression(cmd.expression, actions.holdSeconds * 1000)
    }
    startMotion(m, seq)
  }
  // A finished motion leaves its last frame in the parameters, and a model without an idle group has
  // nothing to move them back (e.g. a sleep motion ending with the head tilted): ease back to defaults.
  const core = model.internalModel.coreModel as unknown as {
    getParameterCount(): number
    getParameterDefaultValue(i: number): number
    getParameterValueByIndex(i: number): number
    setParameterValueByIndex(i: number, v: number): void
  }
  const SETTLE_MS = 500
  let settle: { t0: number; from: [index: number, value: number, target: number][] } | null = null
  model.internalModel.motionManager.on('motionStart', () => {
    settle = null
  })
  model.internalModel.motionManager.on('motionFinish', () => {
    if (looping) {
      // Restart once the library has marked this one complete (it refuses a motion that is "still playing").
      const m = looping
      const seq = playSeq
      void Promise.resolve().then(() => {
        if (looping === m && seq === playSeq) startMotion(m, seq)
      })
      return
    }
    if (motionExpression) clearExpression()
    const from: [number, number, number][] = []
    for (let i = 0; i < core.getParameterCount(); i++) {
      const v = core.getParameterValueByIndex(i)
      const d = core.getParameterDefaultValue(i)
      if (Math.abs(v - d) > 1e-4) from.push([i, v, d])
    }
    settle = from.length > 0 ? { t0: performance.now(), from } : null
  })
  // Written right after the motion step so the values land in the saved (persistent) parameter layer.
  model.internalModel.on('afterMotionUpdate', () => {
    if (!settle) return
    const t = Math.min(1, (performance.now() - settle.t0) / SETTLE_MS)
    const k = t * t * (3 - 2 * t)
    for (const [i, v, d] of settle.from) core.setParameterValueByIndex(i, v + (d - v) * k)
    if (t >= 1) settle = null
  })
  window.pet.onPlay(play)

  // --- Chat bubble ---
  let bubbleTimer: number | null = null
  const hideBubble = (): void => {
    if (bubbleTimer !== null) clearTimeout(bubbleTimer)
    bubbleTimer = null
    bubble.classList.remove('show')
  }
  // While speech plays, the bubble stays up no matter how long the reply is.
  let speaking = false
  const showBubble = (text: string, ms: number | null, stickerUrl?: string): void => {
    if (bubbleTimer !== null) clearTimeout(bubbleTimer)
    bubbleTimer = ms === null || speaking ? null : window.setTimeout(hideBubble, ms)
    bubble.textContent = text
    if (stickerUrl?.startsWith('data:image/')) {
      const img = document.createElement('img')
      img.alt = ''
      img.style.marginTop = text ? '6px' : '0'
      // The picture's size is only known once it has loaded.
      img.addEventListener('load', positionBubble)
      img.src = stickerUrl
      bubble.appendChild(img)
    }
    positionBubble()
    bubble.classList.add('show')
  }
  // --- Status value pops (好感度 +0.3 …): float up at the right of the head, with the short reason ---
  const positionStatus = (): void => {
    const w = natural.width * model.scale.x
    const h = natural.height * model.scale.y
    const headRight = model.x + w * (0.5 + HEAD_HALF_WIDTH_RATIO)
    const headY = model.y + h * HEAD_Y_RATIO
    const maxLeft = Math.max(BUBBLE_MARGIN_PX, window.innerWidth - statusFx.offsetWidth - BUBBLE_MARGIN_PX)
    statusFx.style.left = `${clamp(headRight + 4, BUBBLE_MARGIN_PX, maxLeft)}px`
    statusFx.style.bottom = `${clamp(window.innerHeight - headY, BUBBLE_MARGIN_PX, window.innerHeight - BUBBLE_MARGIN_PX)}px`
    // Fishing HUD: same column, just below the status pops (which stack upwards from eye level).
    if (fishingHud?.visible()) {
      const fw = fishingEl.offsetWidth
      const fh = fishingEl.offsetHeight
      fishingEl.style.left = `${clamp(headRight + 4, BUBBLE_MARGIN_PX, Math.max(BUBBLE_MARGIN_PX, window.innerWidth - fw - BUBBLE_MARGIN_PX))}px`
      fishingEl.style.top = `${clamp(headY + 6, BUBBLE_MARGIN_PX, Math.max(BUBBLE_MARGIN_PX, window.innerHeight - fh - BUBBLE_MARGIN_PX))}px`
    }
  }
  fishingHud = createFishingHud(
    fishingEl,
    (seq, clicked) => window.pet.fishingHook(seq, clicked),
    () => {
      positionStatus()
      hitTest()
    }
  )
  window.pet.onFishing((m) => fishingHud?.handle(m))
  // A layout change (scale / framing from the console) moves the head without resizing the window.
  window.pet.onLayout(() => requestAnimationFrame(positionStatus))
  const el = (cls: string, text = ''): HTMLDivElement => {
    const d = document.createElement('div')
    d.className = cls
    d.textContent = text
    return d
  }
  const showStatus = (changes: StatusChange[]): void => {
    changes.forEach((c, i) => {
      const up = c.delta > 0
      const pop = el(`status-pop ${up ? 'up' : 'down'}${c.tier ? ' tier' : ''}`)
      // One pop per value: a newer change of the same value replaces its pop, other values keep theirs.
      statusFx.querySelectorAll<HTMLElement>('.status-pop').forEach((old) => {
        if (old.dataset.stat === c.id) old.remove()
      })
      pop.dataset.stat = c.id
      pop.style.setProperty('--s-color', c.color)
      pop.style.animationDelay = `${i * 0.25}s`
      const head = el('sp-head')
      head.append(el('sp-icon', up ? '▲' : '▼'), el('sp-name', c.name), el('sp-delta', `${up ? '+' : ''}${+c.delta.toFixed(2)}`))
      const bar = el('sp-bar')
      const fill = document.createElement('i')
      fill.style.width = `${(c.ratio * 100).toFixed(1)}%`
      bar.append(fill)
      pop.append(head, bar)
      if (c.reason) pop.append(el('sp-reason', c.reason))
      if (c.tier) pop.append(el('sp-tier', `✦ ${up ? '进入' : '跌到'}「${c.tier}」`))
      pop.addEventListener('animationend', (e) => {
        if (e.target === pop) pop.remove()
      })
      // animationend never fires while the window is hidden or animations are disabled.
      window.setTimeout(() => pop.remove(), (c.tier ? 5500 : 3600) + i * 250 + 500)
      statusFx.append(pop)
    })
    positionStatus()
  }
  window.addEventListener('resize', positionStatus)

  const thinkingShown = (): boolean => bubble.classList.contains('show') && bubble.textContent === THINKING_TEXT

  window.pet.onOutput((out: AgentOutput) => {
    if (out.text) wake()
    if (out.status?.length) showStatus(out.status)
    if (out.text || out.stickerUrl) showBubble(out.text, Math.min(15000, (out.stickerUrl ? 5000 : 3000) + out.text.length * 180), out.stickerUrl)
    else if (thinkingShown()) hideBubble()
    play({
      expression: out.expression && Object.hasOwn(actions.expressions, out.expression) ? actions.expressions[out.expression] : null,
      motion: out.motion && Object.hasOwn(actions.motions, out.motion) ? actions.motions[out.motion] : null
    })
  })

  // --- Voice: microphone, streamed TTS playback, lip sync ---
  let online = false
  let micOn = false
  let heardText: string | null = null
  let heardTimer: number | null = null
  const refreshPlaceholder = (): void => {
    if (heardText) chatInput.placeholder = `听到：${heardText}`
    else if (!online) chatInput.placeholder = '后端未连接'
    else chatInput.placeholder = micOn ? '在听哦，直接说话或打字…' : '和我说点什么…（回车发送，Shift+回车换行）'
  }
  const showHeard = (text: string): void => {
    heardText = text
    if (heardTimer !== null) clearTimeout(heardTimer)
    heardTimer = window.setTimeout(() => {
      heardText = null
      heardTimer = null
      refreshPlaceholder()
    }, HEARD_SHOW_MS)
    refreshPlaceholder()
  }

  const player = new SpeechPlayer((playing) => {
    speaking = playing
    if (playing && bubbleTimer !== null) {
      clearTimeout(bubbleTimer)
      bubbleTimer = null
    } else if (!playing && bubble.classList.contains('show')) {
      bubbleTimer = window.setTimeout(hideBubble, 3000)
    }
    window.pet.setPlayback(playing)
  })
  const mic = new MicCapture((pcm) => window.pet.sendMic(pcm))
  const setMic = (listen: boolean): void => {
    micOn = listen
    micDot.classList.toggle('on', listen)
    if (!listen) micDot.classList.remove('hearing')
    refreshPlaceholder()
    if (!listen) return mic.stop()
    mic.start().catch((e: unknown) => {
      const msg = e instanceof Error ? `${e.name}: ${e.message}` : String(e)
      window.pet.micError(msg)
      micOn = false
      micDot.classList.remove('on', 'hearing')
      refreshPlaceholder()
      showBubble('（麦克风打不开：请检查 Windows 设置 → 隐私和安全性 → 麦克风）', 8000)
    })
  }

  // Mouth follows the audible TTS level; when silent, motions keep control of the mouth.
  const mouth = new MouthFollower()
  // Cubism 4 specifics not exposed on the generic InternalModel type.
  const im = model.internalModel as unknown as {
    motionManager: { lipSyncIds?: string[] }
    coreModel: { setParameterValueById(id: string, value: number): void }
  }
  const lipIds = im.motionManager.lipSyncIds?.length ? [...im.motionManager.lipSyncIds] : ['ParamMouthOpenY']
  model.internalModel.on('beforeModelUpdate', () => {
    const v = mouth.next(player.level())
    if (v <= 0) return
    for (const id of lipIds) im.coreModel.setParameterValueById(id, v)
  })

  const reportedTtsErrors = new Set<string>()
  window.pet.onVoice((m) => {
    switch (m.type) {
      case 'mic':
        setMic(m.listen)
        break
      case 'camera':
        camDot.classList.toggle('on', m.on)
        camDot.textContent = m.present === true ? '摄像头开启中 · 看到你了' : m.present === false ? '摄像头开启中 · 没看到人' : '摄像头开启中'
        camDot.classList.toggle('seen', m.present === true)
        break
      case 'hearing':
        micDot.classList.toggle('hearing', m.active && micOn)
        if (m.active && micOn) wake()
        break
      case 'heard':
        showHeard(m.text)
        break
      case 'interrupt':
        player.stop()
        break
      case 'audio_start':
        player.begin(m.id, m.sample_rate)
        break
      case 'audio_end':
        player.end(m.id)
        // Report each distinct failure once, appended so the reply text stays readable.
        if (m.error && m.error !== 'interrupted' && !reportedTtsErrors.has(m.error)) {
          reportedTtsErrors.add(m.error)
          console.warn('[voice] 语音合成失败：', m.error)
          if (bubble.classList.contains('show')) bubble.textContent += `
（语音：${m.error}）`
        }
        break
    }
  })
  window.pet.onAudio((chunk) => player.push(chunk))

  // --- Chat input ---
  const applyStatus = (st: LinkStatus): void => {
    online = st.state === 'online'
    refreshPlaceholder()
    if (st.thinking) {
      if (!bubble.classList.contains('show')) showBubble(THINKING_TEXT, null)
    } else if (thinkingShown()) {
      hideBubble()
    }
  }
  applyStatus({ state: 'offline', thinking: false })
  window.pet.onLinkStatus(applyStatus)
  // Pushes sent while the model was loading were dropped (no listener yet); catch up now.
  void window.pet.getState().then((st) => {
    applyStatus(st.link)
    setMic(st.micListen)
    fishingHud?.handle(st.fishing)
  })

  // --- Picture attached to the next message (paste with Ctrl+V or drop onto the window) ---
  let pendingImage: string | null = null
  const setPendingImage = (dataUrl: string | null): void => {
    pendingImage = dataUrl
    attach.classList.toggle('on', dataUrl !== null)
    if (dataUrl) attachImg.src = dataUrl
    else attachImg.removeAttribute('src')
  }
  // Re-encoded as a downscaled JPEG: keeps it small, drops metadata (EXIF location etc.) and flattens GIF/WebP.
  const toJpegDataUrl = async (file: File): Promise<string> => {
    const bmp = await createImageBitmap(file)
    const k = Math.min(1, IMAGE_MAX_SIDE_PX / Math.max(bmp.width, bmp.height))
    const c = document.createElement('canvas')
    c.width = Math.max(1, Math.round(bmp.width * k))
    c.height = Math.max(1, Math.round(bmp.height * k))
    const ctx = c.getContext('2d')
    if (!ctx) throw new Error('canvas unavailable')
    ctx.fillStyle = '#fff'
    ctx.fillRect(0, 0, c.width, c.height)
    ctx.drawImage(bmp, 0, 0, c.width, c.height)
    bmp.close()
    return c.toDataURL('image/jpeg', IMAGE_JPEG_QUALITY)
  }
  const takeImageFile = async (file: File | undefined | null): Promise<boolean> => {
    if (!file || !file.type.startsWith('image/')) return false
    try {
      setPendingImage(await toJpegDataUrl(file))
      chatInput.focus()
    } catch (err) {
      showBubble(`（这张图片打不开：${err instanceof Error ? err.message : String(err)}）`, 4000)
    }
    return true
  }
  const imageOf = (dt: DataTransfer | null): File | undefined => Array.from(dt?.files ?? []).find((f) => f.type.startsWith('image/'))
  chatInput.addEventListener('paste', (e) => {
    const file = imageOf(e.clipboardData)
    if (!file) return // plain text paste keeps working
    e.preventDefault()
    void takeImageFile(file)
  })
  window.addEventListener('dragover', (e) => e.preventDefault())
  window.addEventListener('drop', (e) => {
    e.preventDefault()
    void takeImageFile(imageOf(e.dataTransfer))
  })
  attachRemove.addEventListener('click', () => setPendingImage(null))

  // The box grows with its text (CSS max-height caps it, then it scrolls).
  const fitInput = (): void => {
    chatInput.style.height = 'auto'
    chatInput.style.height = `${chatInput.scrollHeight + chatInput.offsetHeight - chatInput.clientHeight}px`
  }
  chatInput.addEventListener('input', fitInput)

  chat.addEventListener('submit', (e) => {
    e.preventDefault()
    const text = chatInput.value.trim()
    if (pendingImage) {
      wake()
      window.pet.sendImage(text || DEFAULT_IMAGE_PROMPT, pendingImage)
      setPendingImage(null)
      chatInput.value = ''
      fitInput()
      return
    }
    if (!text) return
    wake()
    window.pet.sendText(text)
    chatInput.value = ''
    fitInput()
  })
  chatInput.addEventListener('keydown', (e) => {
    // Enter sends; Shift+Enter starts a new line. Not while an IME is composing (Enter then confirms the candidate).
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault()
      chat.requestSubmit()
      return
    }
    if (e.key === 'Escape') {
      if (pendingImage) setPendingImage(null)
      else chatInput.blur()
    }
  })
  chat.style.display = 'flex'
}

main().catch((e) => showNotice(`启动失败：${e instanceof Error ? e.stack ?? e.message : String(e)}`))
