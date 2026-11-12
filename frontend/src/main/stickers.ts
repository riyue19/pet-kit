import { copyFile, mkdir, readFile, rename, rm, stat, writeFile } from 'node:fs/promises'
import { randomBytes } from 'node:crypto'
import { basename, dirname, extname, join } from 'node:path'
import { parse, stringify } from 'yaml'
import { ConfigError } from './config'
import {
  MAX_STICKERS,
  STICKER_MAX_BYTES,
  STICKER_NAME_FORBIDDEN,
  STICKER_NAME_MAX,
  stickerNameError,
  type Sticker,
  type StickerEntry,
  type StickerPatch
} from '../shared/stickers'

const HEADER = `# 表情包：控制台「提示词」页的「表情包」卡片管理，一般不用手改。
# 图片在 data/stickers/<file>（不进 git）。启用的备注名会发给模型，模型回复里填哪个备注名就发哪张图。
# stickers[].id / file  程序生成，不要改
# stickers[].name       备注名（最长 20 个字，不能重复，不能含引号、反斜杠、{} [] 或换行）
# stickers[].enabled    false = 留着但不告诉模型
`

const ID_RE = /^stk_[0-9a-f]{12}$/
// Also what keeps a hand-edited `file` from pointing outside the sticker folder.
const FILE_RE = /^stk_[0-9a-f]{12}\.(png|jpg|gif|webp)$/
const MIME_EXT: Record<string, string> = { 'image/png': 'png', 'image/jpeg': 'jpg', 'image/gif': 'gif', 'image/webp': 'webp' }

type Obj = Record<string, unknown>
const isObj = (v: unknown): v is Obj => typeof v === 'object' && v !== null && !Array.isArray(v)

/** Image type by content (the file extension is not trusted); null = not a picture we accept. */
export function sniffImage(buf: Uint8Array): string | null {
  const b = Buffer.from(buf.buffer, buf.byteOffset, Math.min(buf.byteLength, 12))
  if (b.length >= 4 && b.readUInt32BE(0) === 0x89504e47) return 'image/png'
  if (b.length >= 3 && b[0] === 0xff && b[1] === 0xd8 && b[2] === 0xff) return 'image/jpeg'
  if (b.length >= 4 && b.toString('latin1', 0, 4) === 'GIF8') return 'image/gif'
  if (b.length >= 12 && b.toString('latin1', 0, 4) === 'RIFF' && b.toString('latin1', 8, 12) === 'WEBP') return 'image/webp'
  return null
}

export async function loadStickers(file: string): Promise<Sticker[]> {
  let text: string
  try {
    text = await readFile(file, 'utf8')
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code === 'ENOENT') return []
    throw e
  }
  let doc: unknown
  try {
    doc = parse(text)
  } catch (e) {
    throw new ConfigError(`${file}: YAML 语法错误：${e instanceof Error ? e.message : String(e)}`)
  }
  const raw = isObj(doc) && Array.isArray(doc.stickers) ? doc.stickers : []
  const out: Sticker[] = []
  for (const r of raw) {
    if (out.length >= MAX_STICKERS) break
    const bad =
      !isObj(r) ||
      typeof r.id !== 'string' ||
      !ID_RE.test(r.id) ||
      typeof r.file !== 'string' ||
      !FILE_RE.test(r.file) ||
      typeof r.name !== 'string' ||
      stickerNameError(
        r.name,
        out.map((s) => s.name)
      ) !== null ||
      out.some((s) => s.id === r.id)
    if (bad) {
      console.warn('[stickers] 跳过 stickers.yaml 里一条无效的表情包')
      continue
    }
    const o = r as Obj
    out.push({ id: o.id as string, file: o.file as string, name: (o.name as string).trim(), enabled: o.enabled !== false })
  }
  return out
}

export async function saveStickers(file: string, stickers: Sticker[]): Promise<void> {
  await mkdir(dirname(file), { recursive: true })
  const tmp = file + '.tmp'
  try {
    await writeFile(tmp, HEADER + '\n' + stringify({ stickers }), 'utf8')
    await rename(tmp, file)
  } catch (e) {
    await rm(tmp, { force: true }).catch(() => undefined)
    throw e
  }
}

/** A usable, unique note name derived from a file name. */
export function defaultStickerName(base: string, taken: readonly string[]): string {
  const clean = [...base.replace(new RegExp(STICKER_NAME_FORBIDDEN.source, 'g'), '').trim()]
  const stem = clean.length > 0 ? clean : [...'表情']
  for (let n = 1; ; n++) {
    const suffix = n === 1 ? '' : String(n)
    const name = stem.slice(0, STICKER_NAME_MAX - suffix.length).join('') + suffix
    if (!taken.includes(name)) return name
  }
}

/** Copies the pictures into `dir` and appends them to the index. Files that can't be used are reported, not fatal. */
export async function importStickerFiles(
  paths: readonly string[],
  file: string,
  dir: string
): Promise<{ stickers: Sticker[]; errors: string[] }> {
  const stickers = await loadStickers(file)
  const errors: string[] = []
  let added = 0
  await mkdir(dir, { recursive: true })
  for (const p of paths) {
    const shown = basename(p)
    if (stickers.length >= MAX_STICKERS) {
      errors.push(`${shown}：表情包最多 ${MAX_STICKERS} 张，没有导入`)
      continue
    }
    try {
      const info = await stat(p)
      if (!info.isFile()) throw new Error('不是文件')
      if (info.size > STICKER_MAX_BYTES) throw new Error(`超过 ${Math.round(STICKER_MAX_BYTES / 1024 / 1024)} MB`)
      const mime = sniffImage(await readFile(p))
      if (!mime) throw new Error('不是 PNG / JPG / GIF / WebP 图片')
      const id = `stk_${randomBytes(6).toString('hex')}`
      const name = `${id}.${MIME_EXT[mime]}`
      await copyFile(p, join(dir, name))
      stickers.push({
        id,
        file: name,
        name: defaultStickerName(
          basename(p, extname(p)),
          stickers.map((s) => s.name)
        ),
        enabled: true
      })
      added++
    } catch (e) {
      errors.push(`${shown}：${e instanceof Error ? e.message : String(e)}`)
    }
  }
  if (added > 0) await saveStickers(file, stickers)
  return { stickers, errors }
}

export async function updateSticker(file: string, id: string, patch: StickerPatch): Promise<Sticker[]> {
  const stickers = await loadStickers(file)
  const s = stickers.find((x) => x.id === id)
  if (!s) throw new Error('这张表情包已经不存在了')
  if (patch.name !== undefined) {
    const err = stickerNameError(
      patch.name,
      stickers.filter((x) => x !== s).map((x) => x.name)
    )
    if (err) throw new Error(err)
    s.name = patch.name.trim()
  }
  if (patch.enabled !== undefined) s.enabled = patch.enabled
  await saveStickers(file, stickers)
  return stickers
}

export async function deleteSticker(file: string, dir: string, id: string): Promise<Sticker[]> {
  const stickers = await loadStickers(file)
  const i = stickers.findIndex((x) => x.id === id)
  if (i < 0) return stickers
  const [gone] = stickers.splice(i, 1)
  await saveStickers(file, stickers)
  await rm(join(dir, gone.file), { force: true }).catch(() => undefined)
  return stickers
}

/** The picture as a data URL; null when it is missing, too large or not an image. */
export async function stickerDataUrl(dir: string, s: Sticker, shrink?: (buf: Buffer) => string | null): Promise<string | null> {
  try {
    const buf = await readFile(join(dir, s.file))
    if (buf.length > STICKER_MAX_BYTES) return null
    const mime = sniffImage(buf)
    if (!mime) return null
    return shrink?.(buf) ?? `data:${mime};base64,${buf.toString('base64')}`
  } catch {
    return null
  }
}

/** Console rows. `shrink` makes a small preview (null = can't, the full picture is used). */
export async function stickerEntries(
  dir: string,
  stickers: readonly Sticker[],
  shrink?: (buf: Buffer) => string | null
): Promise<StickerEntry[]> {
  return Promise.all(
    stickers.map(async (s) => ({ id: s.id, name: s.name, enabled: s.enabled, thumb: (await stickerDataUrl(dir, s, shrink)) ?? '' }))
  )
}
