import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { mkdtemp, readdir, readFile, rm, writeFile } from 'node:fs/promises'
import {
  defaultStickerName,
  deleteSticker,
  importStickerFiles,
  loadStickers,
  sniffImage,
  stickerDataUrl,
  stickerEntries,
  updateSticker
} from '../../frontend/src/main/stickers'
import { MAX_STICKERS, STICKER_MAX_BYTES, stickerNameError } from '../../frontend/src/shared/stickers'

const PNG = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 1, 2, 3, 4])
const GIF = Buffer.from('GIF89a-rest-of-file', 'latin1')
const WEBP = Buffer.concat([Buffer.from('RIFF', 'latin1'), Buffer.from([4, 0, 0, 0]), Buffer.from('WEBPVP8 ', 'latin1')])

let root: string
let index: string
let dir: string
beforeEach(async () => {
  root = await mkdtemp(join(tmpdir(), 'pet-stickers-'))
  index = join(root, 'config', 'stickers.yaml')
  dir = join(root, 'data', 'stickers')
})
afterEach(async () => {
  await rm(root, { recursive: true, force: true })
})

async function source(name: string, content: Buffer): Promise<string> {
  const p = join(root, name)
  await writeFile(p, content)
  return p
}

describe('stickerNameError', () => {
  it('accepts ordinary names and rejects empty, long, unsafe and duplicate ones', () => {
    expect(stickerNameError('猫猫震惊')).toBeNull()
    expect(stickerNameError('  ')).not.toBeNull()
    expect(stickerNameError('字'.repeat(21))).not.toBeNull()
    expect(stickerNameError('字'.repeat(20))).toBeNull()
    for (const bad of ['a"b', "a'b", 'a\\b', 'a{b', 'a]b', 'a\nb']) expect(stickerNameError(bad)).not.toBeNull()
    expect(stickerNameError('好耶', ['好耶'])).not.toBeNull()
  })
})

describe('sniffImage', () => {
  it('goes by content, not by name', () => {
    expect(sniffImage(PNG)).toBe('image/png')
    expect(sniffImage(Buffer.from([0xff, 0xd8, 0xff, 0xe0]))).toBe('image/jpeg')
    expect(sniffImage(GIF)).toBe('image/gif')
    expect(sniffImage(WEBP)).toBe('image/webp')
    expect(sniffImage(Buffer.from('<svg onload=alert(1)>'))).toBeNull()
    expect(sniffImage(Buffer.alloc(0))).toBeNull()
  })
})

describe('defaultStickerName', () => {
  it('cleans, shortens and de-duplicates', () => {
    expect(defaultStickerName('好耶', [])).toBe('好耶')
    expect(defaultStickerName('好耶', ['好耶'])).toBe('好耶2')
    expect(defaultStickerName('a"b{c}', [])).toBe('abc')
    expect(defaultStickerName('""', [])).toBe('表情')
    expect([...defaultStickerName('字'.repeat(30), [])]).toHaveLength(20)
    expect(defaultStickerName('字'.repeat(30), ['字'.repeat(20)])).toBe('字'.repeat(19) + '2')
  })
})

describe('import / update / delete', () => {
  it('missing index means no stickers', async () => {
    expect(await loadStickers(index)).toEqual([])
  })

  it('copies pictures, names them after the file, and reports what it skipped', async () => {
    const r = await importStickerFiles(
      [await source('好耶.png', PNG), await source('fake.png', Buffer.from('not an image')), await source('好耶.gif', GIF), join(root, 'gone.png')],
      index,
      dir
    )
    expect(r.stickers.map((s) => s.name)).toEqual(['好耶', '好耶2'])
    expect(r.stickers.every((s) => s.enabled)).toBe(true)
    expect(r.errors).toHaveLength(2)
    expect(r.errors[0]).toContain('fake.png')
    expect((await readdir(dir)).sort()).toEqual(r.stickers.map((s) => s.file).sort())
    expect(r.stickers[0].file).toMatch(/^stk_[0-9a-f]{12}\.png$/)
    expect(r.stickers[1].file).toMatch(/\.gif$/)
    expect(await loadStickers(index)).toEqual(r.stickers)
    expect((await readFile(index, 'utf8')).startsWith('# 表情包')).toBe(true)
  })

  it('rejects oversized files and stops at the limit', async () => {
    const big = await source('big.png', Buffer.concat([PNG, Buffer.alloc(STICKER_MAX_BYTES)]))
    expect((await importStickerFiles([big], index, dir)).errors[0]).toContain('MB')
    const many: string[] = []
    for (let i = 0; i < MAX_STICKERS + 1; i++) many.push(await source(`s${i}.png`, PNG))
    const r = await importStickerFiles(many, index, dir)
    expect(r.stickers).toHaveLength(MAX_STICKERS)
    expect(r.errors).toHaveLength(1)
  })

  it('renames, toggles and deletes', async () => {
    const { stickers } = await importStickerFiles([await source('a.png', PNG), await source('b.png', PNG)], index, dir)
    const [a, b] = stickers
    expect((await updateSticker(index, a.id, { name: ' 猫猫震惊 ' }))[0].name).toBe('猫猫震惊')
    await expect(updateSticker(index, b.id, { name: '猫猫震惊' })).rejects.toThrow(/已经有/)
    await expect(updateSticker(index, b.id, { name: 'x"y' })).rejects.toThrow()
    await expect(updateSticker(index, 'stk_000000000000', { enabled: false })).rejects.toThrow(/不存在/)
    expect((await updateSticker(index, b.id, { enabled: false }))[1].enabled).toBe(false)
    const left = await deleteSticker(index, dir, a.id)
    expect(left.map((s) => s.id)).toEqual([b.id])
    expect(await readdir(dir)).toEqual([b.file])
    expect(await loadStickers(index)).toEqual(left)
  })

  it('skips hand-edited entries that are invalid or point outside the folder', async () => {
    const { stickers } = await importStickerFiles([await source('a.png', PNG)], index, dir)
    const ok = stickers[0]
    await writeFile(
      index,
      `stickers:\n  - {id: ${ok.id}, file: ${ok.file}, name: 好耶}\n  - {id: stk_aaaaaaaaaaaa, file: ../../secret.png, name: 坏}\n  - {id: stk_bbbbbbbbbbbb, file: stk_bbbbbbbbbbbb.png, name: 好耶}\n  - nonsense\n`,
      'utf8'
    )
    expect(await loadStickers(index)).toEqual([{ ...ok, name: '好耶', enabled: true }])
  })
})

describe('stickerDataUrl / stickerEntries', () => {
  it('returns a data URL, or nothing when the picture is gone', async () => {
    const { stickers } = await importStickerFiles([await source('a.png', PNG)], index, dir)
    expect(await stickerDataUrl(dir, stickers[0])).toBe(`data:image/png;base64,${PNG.toString('base64')}`)
    expect(await stickerDataUrl(dir, stickers[0], () => 'data:image/png;base64,small')).toBe('data:image/png;base64,small')
    expect(await stickerDataUrl(dir, stickers[0], () => null)).toBe(`data:image/png;base64,${PNG.toString('base64')}`)
    await rm(join(dir, stickers[0].file))
    expect(await stickerDataUrl(dir, stickers[0])).toBeNull()
    expect(await stickerEntries(dir, stickers)).toEqual([{ id: stickers[0].id, name: 'a', enabled: true, thumb: '' }])
  })
})
