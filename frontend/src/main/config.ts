import { readFile, rename, rm, writeFile } from 'node:fs/promises'
import { parse, parseDocument } from 'yaml'
import type { Framing, ModelSettings } from '../shared/ipc'

export type ModelConfig = ModelSettings

export const HALF_RATIO_RANGE = { min: 0.2, max: 1 }

export interface FrontendConfig {
  model: ModelConfig
  window: {
    width: number
    height: number
    x: number | null
    y: number | null
    alwaysOnTop: boolean
  }
  eyeTracking: { fps: number }
  hitTest: { alphaThreshold: number }
}

export class ConfigError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'ConfigError'
  }
}

export const DEFAULT_CONFIG: FrontendConfig = {
  model: { path: '', scale: 'auto', framing: 'full', halfRatio: 0.55 },
  window: { width: 400, height: 600, x: null, y: null, alwaysOnTop: true },
  eyeTracking: { fps: 30 },
  hitTest: { alphaThreshold: 10 }
}

function cloneDefault(): FrontendConfig {
  return JSON.parse(JSON.stringify(DEFAULT_CONFIG)) as FrontendConfig
}

type Obj = Record<string, unknown>

function isObj(v: unknown): v is Obj {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

function section(root: Obj, name: string): Obj {
  const v = root[name]
  if (v === undefined || v === null) return {}
  if (!isObj(v)) throw new ConfigError(`${name}: must be a mapping`)
  return v
}

function has(o: Obj, key: string): boolean {
  return o[key] !== undefined
}

function intInRange(v: unknown, path: string, min: number, max: number): number {
  if (typeof v !== 'number' || !Number.isInteger(v) || v < min || v > max) {
    throw new ConfigError(`${path}: must be an integer in [${min}, ${max}]`)
  }
  return v
}

function validateModelPath(v: unknown): string {
  const p = 'model.path'
  if (typeof v !== 'string') throw new ConfigError(`${p}: must be a string`)
  if (v === '') return v
  if (!v.endsWith('.model3.json')) throw new ConfigError(`${p}: must end with ".model3.json"`)
  if (v.startsWith('/') || v.startsWith('\\') || /^[a-zA-Z]:/.test(v)) {
    throw new ConfigError(`${p}: must be a relative path`)
  }
  if (v.split(/[/\\]/).includes('..')) {
    throw new ConfigError(`${p}: must not contain ".." segments`)
  }
  return v
}

function validateScale(v: unknown): number | 'auto' {
  const p = 'model.scale'
  if (v === 'auto') return 'auto'
  if (typeof v === 'number' && Number.isFinite(v) && v > 0 && v <= 10) return v
  throw new ConfigError(`${p}: must be "auto" or a number in (0, 10]`)
}

function validateFraming(v: unknown): Framing {
  if (v === 'full' || v === 'half') return v
  throw new ConfigError('model.framing: must be "full" or "half"')
}

function validateHalfRatio(v: unknown): number {
  const { min, max } = HALF_RATIO_RANGE
  if (typeof v === 'number' && Number.isFinite(v) && v >= min && v <= max) return v
  throw new ConfigError(`model.half_ratio: must be a number in [${min}, ${max}]`)
}

function validateXY(v: unknown, path: string): number | null {
  if (v === null) return null
  if (typeof v === 'number' && Number.isInteger(v)) return v
  throw new ConfigError(`${path}: must be an integer or null`)
}

export function parseFrontendConfig(yamlText: string): FrontendConfig {
  let doc: unknown
  try {
    doc = parse(yamlText)
  } catch (e) {
    throw new ConfigError(`YAML syntax error: ${e instanceof Error ? e.message : String(e)}`)
  }
  const cfg = cloneDefault()
  if (doc === undefined || doc === null) return cfg
  if (!isObj(doc)) throw new ConfigError('<root>: must be a mapping')

  const model = section(doc, 'model')
  if (has(model, 'path')) cfg.model.path = validateModelPath(model.path)
  if (has(model, 'scale')) cfg.model.scale = validateScale(model.scale)
  if (has(model, 'framing')) cfg.model.framing = validateFraming(model.framing)
  if (has(model, 'half_ratio')) cfg.model.halfRatio = validateHalfRatio(model.half_ratio)

  const win = section(doc, 'window')
  if (has(win, 'width')) cfg.window.width = intInRange(win.width, 'window.width', 100, 4000)
  if (has(win, 'height')) cfg.window.height = intInRange(win.height, 'window.height', 100, 4000)
  if (has(win, 'x')) cfg.window.x = validateXY(win.x, 'window.x')
  if (has(win, 'y')) cfg.window.y = validateXY(win.y, 'window.y')
  if (has(win, 'always_on_top')) {
    if (typeof win.always_on_top !== 'boolean') {
      throw new ConfigError('window.always_on_top: must be a boolean')
    }
    cfg.window.alwaysOnTop = win.always_on_top
  }

  const eye = section(doc, 'eye_tracking')
  if (has(eye, 'fps')) cfg.eyeTracking.fps = intInRange(eye.fps, 'eye_tracking.fps', 1, 60)

  const hit = section(doc, 'hit_test')
  if (has(hit, 'alpha_threshold')) {
    cfg.hitTest.alphaThreshold = intInRange(hit.alpha_threshold, 'hit_test.alpha_threshold', 0, 255)
  }

  return cfg
}

export async function loadFrontendConfig(filePath: string): Promise<FrontendConfig> {
  let text: string
  try {
    text = await readFile(filePath, 'utf8')
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code === 'ENOENT') return cloneDefault()
    throw e
  }
  return parseFrontendConfig(text)
}

export async function updateModelConfig(
  filePath: string,
  model: ModelConfig
): Promise<FrontendConfig> {
  let original = ''
  try {
    original = await readFile(filePath, 'utf8')
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code !== 'ENOENT') throw e
  }

  const doc = parseDocument(original)
  if (doc.errors.length > 0) {
    throw new ConfigError(`YAML syntax error: ${doc.errors[0].message}`)
  }
  try {
    doc.setIn(['model', 'path'], model.path)
    doc.setIn(['model', 'scale'], model.scale)
    doc.setIn(['model', 'framing'], model.framing)
    doc.setIn(['model', 'half_ratio'], model.halfRatio)
  } catch (e) {
    throw new ConfigError(`model: cannot update (${e instanceof Error ? e.message : String(e)})`)
  }

  const text = doc.toString()
  const cfg = parseFrontendConfig(text)

  const tmp = filePath + '.tmp'
  try {
    await writeFile(tmp, text, 'utf8')
    await rename(tmp, filePath)
  } catch (e) {
    await rm(tmp, { force: true }).catch(() => undefined)
    throw e
  }
  return cfg
}
