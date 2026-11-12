import { cp, mkdir, readdir, readFile, stat, writeFile } from 'node:fs/promises'
import { basename, dirname, isAbsolute, join, posix, relative, sep } from 'node:path'

import type { ModelEntry } from '../shared/ipc'

export type { ModelEntry }

export const MODELS_SUBDIR = 'live2d/models'

const MODEL_SUFFIX = '.model3.json'
const EXPRESSION_SUFFIX = '.exp3.json'
const STANDARD_GROUP_IDS: Record<string, string[]> = {
  EyeBlink: ['ParamEyeLOpen', 'ParamEyeROpen'],
  LipSync: ['ParamMouthOpenY']
}

type Json = Record<string, unknown>
const isObject = (v: unknown): v is Json => typeof v === 'object' && v !== null && !Array.isArray(v)

// VTube Studio exports leave EyeBlink/LipSync empty (face tracking drives them) and often don't list their
// .exp3.json files. paramIds null = unknown, assume the standard ids exist. Returns null if nothing changed.
export function completeModelSettings(
  settings: unknown,
  expressionFiles: string[],
  paramIds: Set<string> | null
): Json | null {
  if (!isObject(settings) || !isObject(settings.FileReferences)) return null
  const out = structuredClone(settings) as Json & { FileReferences: Json }
  let changed = false

  const groups = Array.isArray(out.Groups) ? out.Groups.filter(isObject) : []
  for (const [name, standard] of Object.entries(STANDARD_GROUP_IDS)) {
    const ids = standard.filter((id) => !paramIds || paramIds.has(id))
    if (ids.length === 0) continue
    const group = groups.find((g) => g.Name === name)
    if (!group) groups.push({ Target: 'Parameter', Name: name, Ids: ids })
    else if (!Array.isArray(group.Ids) || group.Ids.length === 0) group.Ids = ids
    else continue
    changed = true
  }
  if (changed) out.Groups = groups

  const refs = out.FileReferences
  const expressions = Array.isArray(refs.Expressions) ? refs.Expressions.filter(isObject) : []
  const listed = new Set(expressions.map((e) => String(e.File ?? '').toLowerCase()))
  const names = new Set(expressions.map((e) => String(e.Name ?? '')))
  for (const file of [...expressionFiles].sort()) {
    if (listed.has(file.toLowerCase())) continue
    const base = posix.basename(file).slice(0, -EXPRESSION_SUFFIX.length)
    let name = base
    for (let n = 2; names.has(name); n++) name = `${base}-${n}`
    names.add(name)
    expressions.push({ Name: name, File: file })
    changed = true
  }
  if (expressions.length > 0) refs.Expressions = expressions

  return changed ? out : null
}

async function listFilesWithSuffix(dir: string, suffix: string, prefix = ''): Promise<string[]> {
  const result: string[] = []
  for (const c of await readdir(dir, { withFileTypes: true })) {
    const rel = prefix ? `${prefix}/${c.name}` : c.name
    if (c.isDirectory()) result.push(...(await listFilesWithSuffix(join(dir, c.name), suffix, rel)))
    else if (c.isFile() && c.name.toLowerCase().endsWith(suffix)) result.push(rel)
  }
  return result
}

async function readParamIds(modelDir: string, settings: Json): Promise<Set<string> | null> {
  const refs = settings.FileReferences
  const file = isObject(refs) && typeof refs.DisplayInfo === 'string' ? refs.DisplayInfo : null
  if (!file) return null
  try {
    const cdi: unknown = JSON.parse(await readFile(join(modelDir, file), 'utf8'))
    if (!isObject(cdi) || !Array.isArray(cdi.Parameters)) return null
    return new Set(cdi.Parameters.filter(isObject).map((p) => String(p.Id)))
  } catch (e) {
    console.warn('[models] 读取 cdi3.json 失败：', e)
    return null
  }
}

// Best effort: a model3.json we can't parse is left as-is and the renderer reports the load error.
async function completeImportedModel(model3JsonPath: string): Promise<void> {
  let settings: unknown
  try {
    settings = JSON.parse(await readFile(model3JsonPath, 'utf8'))
  } catch {
    return
  }
  if (!isObject(settings)) return
  const dir = dirname(model3JsonPath)
  const patched = completeModelSettings(
    settings,
    await listFilesWithSuffix(dir, EXPRESSION_SUFFIX),
    await readParamIds(dir, settings)
  )
  if (patched) await writeFile(model3JsonPath, JSON.stringify(patched, null, '\t'))
}

function modelsRoot(publicDir: string): string {
  return join(publicDir, ...MODELS_SUBDIR.split('/'))
}

async function pathExists(p: string): Promise<boolean> {
  try {
    await stat(p)
    return true
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code === 'ENOENT') return false
    throw e
  }
}

export const IMPORT_LIMITS = { maxFiles: 2000, maxBytes: 512 * 1024 * 1024 }

// Guards against importing a model3.json that sits loose in e.g. Desktop/Downloads, which would copy the whole folder.
async function assertReasonableSize(dir: string): Promise<void> {
  let files = 0
  let bytes = 0
  const walk = async (d: string): Promise<void> => {
    for (const c of await readdir(d, { withFileTypes: true })) {
      const p = join(d, c.name)
      if (c.isDirectory()) await walk(p)
      else if (c.isFile()) {
        files++
        bytes += (await stat(p)).size
      }
      if (files > IMPORT_LIMITS.maxFiles || bytes > IMPORT_LIMITS.maxBytes) {
        throw new Error(`模型目录过大（超过 ${IMPORT_LIMITS.maxFiles} 个文件或 512MB）：${dir}\n请把模型单独放进一个文件夹再导入`)
      }
    }
  }
  await walk(dir)
}

/** 列出某个一级子目录里的模型条目（不递归）。 */
async function entriesInDir(modelsDir: string, dirName: string): Promise<ModelEntry[]> {
  let children
  try {
    children = await readdir(join(modelsDir, dirName), { withFileTypes: true })
  } catch (e) {
    const code = (e as NodeJS.ErrnoException).code
    if (code === 'ENOENT' || code === 'ENOTDIR') return []
    throw e
  }
  const files = children
    .filter((c) => c.isFile() && c.name.endsWith(MODEL_SUFFIX))
    .map((c) => c.name)
    .sort()
  const multiple = files.length > 1
  return files.map((file) => ({
    name: multiple ? `${dirName}/${file.slice(0, -MODEL_SUFFIX.length)}` : dirName,
    path: `${MODELS_SUBDIR}/${dirName}/${file}`
  }))
}

export async function listModels(publicDir: string): Promise<ModelEntry[]> {
  const root = modelsRoot(publicDir)
  let dirs
  try {
    dirs = await readdir(root, { withFileTypes: true })
  } catch (e) {
    const code = (e as NodeJS.ErrnoException).code
    if (code === 'ENOENT' || code === 'ENOTDIR') return []
    throw e
  }
  const result: ModelEntry[] = []
  for (const d of dirs) {
    if (!d.isDirectory()) continue
    result.push(...(await entriesInDir(root, d.name)))
  }
  result.sort((a, b) => a.name.localeCompare(b.name))
  return result
}

export async function importModel(model3JsonPath: string, publicDir: string): Promise<ModelEntry> {
  if (!model3JsonPath.endsWith(MODEL_SUFFIX)) {
    throw new Error(`模型文件必须以 ${MODEL_SUFFIX} 结尾`)
  }
  let st
  try {
    st = await stat(model3JsonPath)
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code === 'ENOENT') {
      throw new Error(`模型文件不存在：${model3JsonPath}`)
    }
    throw e
  }
  if (!st.isFile()) throw new Error(`路径不是文件：${model3JsonPath}`)

  const srcDir = dirname(model3JsonPath)
  const fileName = basename(model3JsonPath)
  const root = modelsRoot(publicDir)

  const findEntry = async (dirName: string): Promise<ModelEntry> => {
    const entries = await entriesInDir(root, dirName)
    const wanted = `${MODELS_SUBDIR}/${dirName}/${fileName}`
    const hit =
      entries.find((e) => e.path === wanted) ??
      entries.find((e) => e.path.toLowerCase() === wanted.toLowerCase())
    if (!hit) throw new Error(`未能在模型目录中找到导入的模型：${fileName}`)
    return hit
  }

  const rel = relative(root, srcDir)
  const inside = !rel.startsWith('..') && !isAbsolute(rel)
  if (inside) {
    if (rel === '') {
      throw new Error('模型文件不能直接放在模型库根目录下，请放入子目录')
    }
    const segments = rel.split(sep)
    if (segments.length === 1) {
      // 已经在模型库的一级子目录里，无需复制
      return findEntry(segments[0])
    }
    // 更深层的目录：listModels 不会列出，复制成新的一级目录
  }

  await assertReasonableSize(srcDir)

  let base = basename(srcDir).replace(/[^\p{L}\p{N}._-]/gu, '_')
  if (/^\.*$/.test(base)) base = 'model'
  let destName = base
  for (let n = 2; await pathExists(join(root, destName)); n++) {
    destName = `${base}-${n}`
  }

  await mkdir(root, { recursive: true })
  await cp(srcDir, join(root, destName), { recursive: true, errorOnExist: true, force: false })
  await completeImportedModel(join(root, destName, fileName))

  const entry = (await listModels(publicDir)).find(
    (e) => e.path === `${MODELS_SUBDIR}/${destName}/${fileName}`
  )
  if (!entry) throw new Error(`导入后未能找到模型：${fileName}`)
  return entry
}
