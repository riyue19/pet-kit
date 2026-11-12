import { mkdir, readFile, rename, rm, writeFile } from 'node:fs/promises'
import { basename, dirname, join } from 'node:path'
import { parse, stringify } from 'yaml'
import {
  DEFAULT_ACTION_MAP,
  HOLD_SECONDS_RANGE,
  LABEL_RE,
  type ActionMap,
  type ModelInventory
} from '../shared/agent'
import { ConfigError } from './config'

const MAX_LABELS = 64
const MAX_NAME_LEN = 64
// Same alphabet the importer produces for folder names (letters of any language, digits, . _ - and space).
const DIR_NAME_RE = /^[\p{L}\p{N}_\-. ]{1,64}$/u

const HEADER = `# 表情/动作映射：语义标签 → 模型自带的表情/动作。LLM 只会使用这里出现的标签。
# 控制台「形象」页可视化编辑。
# expressions: 标签 → model3.json 里 Expressions[].Name
# motions:     标签 → { group: 动作组名, index: 组内序号, expression: 配套表情（可选，播动作时显示、播完收起）, loop: 循环（可选，true = 一直保持到下次互动） }
# hold_seconds: 表情保持秒数，之后恢复默认表情
`

type Obj = Record<string, unknown>
type Fail = (msg: string) => never

function isObj(v: unknown): v is Obj {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

function cloneDefault(): ActionMap {
  return JSON.parse(JSON.stringify(DEFAULT_ACTION_MAP)) as ActionMap
}

export function actionsConfigPath(configDir: string, modelPath: string): string | null {
  if (!modelPath) return null
  const dir = basename(dirname(modelPath.replace(/\\/g, '/')))
  if (dir === '.' || dir === '..' || !DIR_NAME_RE.test(dir)) return null
  return join(configDir, 'models', `${dir}.yaml`)
}

function checkLabel(label: string, path: string, fail: Fail): void {
  if (!LABEL_RE.test(label)) {
    fail(`${path}.${label}: 标签只能是小写字母开头的 a-z0-9_，最长 32`)
  }
}

function checkName(v: unknown, path: string, fail: Fail): string {
  if (typeof v !== 'string' || v === '' || v.length > MAX_NAME_LEN) {
    fail(`${path}: 必须是非空字符串，最长 ${MAX_NAME_LEN}`)
  }
  return v as string
}

function checkExpressions(v: unknown, fail: Fail): Record<string, string> {
  if (!isObj(v)) fail('expressions: 必须是映射（标签 → 表情名）')
  const src = v as Obj
  const keys = Object.keys(src)
  if (keys.length > MAX_LABELS) fail(`expressions: 最多 ${MAX_LABELS} 个标签`)
  const out: Record<string, string> = {}
  for (const k of keys) {
    checkLabel(k, 'expressions', fail)
    out[k] = checkName(src[k], `expressions.${k}`, fail)
  }
  return out
}

function checkMotions(v: unknown, fail: Fail): ActionMap['motions'] {
  if (!isObj(v)) fail('motions: 必须是映射（标签 → { group, index }）')
  const src = v as Obj
  const keys = Object.keys(src)
  if (keys.length > MAX_LABELS) fail(`motions: 最多 ${MAX_LABELS} 个标签`)
  const out: ActionMap['motions'] = {}
  for (const k of keys) {
    checkLabel(k, 'motions', fail)
    const path = `motions.${k}`
    const m = src[k]
    if (!isObj(m)) fail(`${path}: 必须是 { group, index } 对象`)
    const mo = m as Obj
    for (const extra of Object.keys(mo)) {
      if (!['group', 'index', 'expression', 'loop'].includes(extra)) fail(`${path}.${extra}: 不支持的字段`)
    }
    const group = checkName(mo.group, `${path}.group`, fail)
    const index = mo.index
    if (typeof index !== 'number' || !Number.isInteger(index) || index < 0) {
      fail(`${path}.index: 必须是 ≥ 0 的整数`)
    }
    out[k] = { group, index: index as number }
    if (mo.expression !== undefined && mo.expression !== null) {
      out[k].expression = checkName(mo.expression, `${path}.expression`, fail)
    }
    if (mo.loop !== undefined && mo.loop !== null) {
      if (typeof mo.loop !== 'boolean') fail(`${path}.loop: 必须是 true 或 false`)
      if (mo.loop) out[k].loop = true
    }
  }
  return out
}

function checkHold(v: unknown, path: string, fail: Fail): number {
  const { min, max } = HOLD_SECONDS_RANGE
  if (typeof v !== 'number' || !Number.isFinite(v) || v < min || v > max) {
    fail(`${path}: 必须是 [${min}, ${max}] 范围内的数字`)
  }
  return v as number
}

function checkKeys(o: Obj, allowed: string[], fail: Fail): void {
  for (const k of Object.keys(o)) {
    if (!allowed.includes(k)) fail(`${k}: 不支持的字段`)
  }
}

export function parseActionMap(raw: unknown): ActionMap {
  const fail: Fail = (msg) => {
    throw new ConfigError(msg)
  }
  const map = cloneDefault()
  if (raw === null || raw === undefined) return map
  if (!isObj(raw)) fail('<root>: 必须是映射')
  const o = raw as Obj
  checkKeys(o, ['expressions', 'motions', 'hold_seconds'], fail)
  if (o.expressions !== undefined && o.expressions !== null) {
    map.expressions = checkExpressions(o.expressions, fail)
  }
  if (o.motions !== undefined && o.motions !== null) {
    map.motions = checkMotions(o.motions, fail)
  }
  if (o.hold_seconds !== undefined && o.hold_seconds !== null) {
    map.holdSeconds = checkHold(o.hold_seconds, 'hold_seconds', fail)
  }
  return map
}

export function validateActionMap(raw: unknown): ActionMap {
  const fail: Fail = (msg) => {
    throw new Error(msg)
  }
  if (!isObj(raw)) fail('映射配置必须是对象')
  const o = raw as Obj
  checkKeys(o, ['expressions', 'motions', 'holdSeconds'], fail)
  for (const k of ['expressions', 'motions', 'holdSeconds']) {
    if (o[k] === undefined) fail(`${k}: 缺少必填字段`)
  }
  return {
    expressions: checkExpressions(o.expressions, fail),
    motions: checkMotions(o.motions, fail),
    holdSeconds: checkHold(o.holdSeconds, 'holdSeconds', fail)
  }
}

export async function loadActionMap(file: string | null): Promise<ActionMap> {
  if (file === null) return cloneDefault()
  let text: string
  try {
    text = await readFile(file, 'utf8')
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code === 'ENOENT') return cloneDefault()
    throw e
  }
  let doc: unknown
  try {
    doc = parse(text)
  } catch (e) {
    throw new ConfigError(`${file}: YAML 语法错误：${e instanceof Error ? e.message : String(e)}`)
  }
  try {
    return parseActionMap(doc)
  } catch (e) {
    throw new ConfigError(`${file}: ${e instanceof Error ? e.message : String(e)}`)
  }
}

export async function saveActionMap(file: string, map: unknown): Promise<ActionMap> {
  const valid = validateActionMap(map)
  await mkdir(dirname(file), { recursive: true })
  const body = stringify({
    expressions: valid.expressions,
    motions: valid.motions,
    hold_seconds: valid.holdSeconds
  })
  const tmp = file + '.tmp'
  try {
    await writeFile(tmp, HEADER + '\n' + body, 'utf8')
    await rename(tmp, file)
  } catch (e) {
    await rm(tmp, { force: true }).catch(() => undefined)
    throw e
  }
  return valid
}

export async function readModelInventory(
  publicDir: string,
  modelPath: string
): Promise<ModelInventory> {
  const empty = (): ModelInventory => ({ expressions: [], motions: [] })
  if (!modelPath) return empty()
  try {
    const text = (await readFile(join(publicDir, modelPath), 'utf8')).replace(/^﻿/, '')
    const json: unknown = JSON.parse(text)
    if (!isObj(json) || !isObj(json.FileReferences)) return empty()
    const refs = json.FileReferences
    const inv = empty()
    if (Array.isArray(refs.Expressions)) {
      for (const e of refs.Expressions) {
        if (isObj(e) && typeof e.Name === 'string') inv.expressions.push(e.Name)
      }
    }
    if (isObj(refs.Motions)) {
      for (const [group, list] of Object.entries(refs.Motions)) {
        if (Array.isArray(list) && list.length > 0) inv.motions.push({ group, count: list.length })
      }
    }
    return inv
  } catch (e) {
    console.warn('[actions] 读取模型表情/动作清单失败：', e)
    return empty()
  }
}
