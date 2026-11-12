import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { mkdir, mkdtemp, readFile, rm, stat, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import {
  IMPORT_LIMITS,
  MODELS_SUBDIR,
  completeModelSettings,
  importModel,
  listModels
} from '../../frontend/src/main/models'

let publicDir: string
let extDir: string

beforeEach(async () => {
  publicDir = await mkdtemp(join(tmpdir(), 'pet-kit-pub-'))
  extDir = await mkdtemp(join(tmpdir(), 'pet-kit-ext-'))
})

afterEach(async () => {
  await rm(publicDir, { recursive: true, force: true })
  await rm(extDir, { recursive: true, force: true })
})

async function touch(file: string, content = '{}'): Promise<void> {
  await mkdir(join(file, '..'), { recursive: true })
  await writeFile(file, content, 'utf8')
}

function modelsDir(): string {
  return join(publicDir, ...MODELS_SUBDIR.split('/'))
}

async function exists(p: string): Promise<boolean> {
  try {
    await stat(p)
    return true
  } catch {
    return false
  }
}

describe('listModels', () => {
  it('returns [] when models dir does not exist', async () => {
    expect(await listModels(publicDir)).toEqual([])
  })

  it('single model uses directory name', async () => {
    await touch(join(modelsDir(), 'Hiyori', 'Hiyori.model3.json'))
    expect(await listModels(publicDir)).toEqual([
      { name: 'Hiyori', path: 'live2d/models/Hiyori/Hiyori.model3.json' }
    ])
  })

  it('multiple models in one directory use "dir/file" names', async () => {
    await touch(join(modelsDir(), 'Pack', 'b.model3.json'))
    await touch(join(modelsDir(), 'Pack', 'a.model3.json'))
    expect(await listModels(publicDir)).toEqual([
      { name: 'Pack/a', path: 'live2d/models/Pack/a.model3.json' },
      { name: 'Pack/b', path: 'live2d/models/Pack/b.model3.json' }
    ])
  })

  it('skips empty directories, stray files, and nested-only models', async () => {
    await mkdir(join(modelsDir(), 'empty'), { recursive: true })
    await touch(join(modelsDir(), 'stray.model3.json'))
    await touch(join(modelsDir(), 'nested', 'deep', 'x.model3.json'))
    await touch(join(modelsDir(), 'other', 'readme.txt'), 'hi')
    await touch(join(modelsDir(), 'ok', 'ok.model3.json'))
    expect(await listModels(publicDir)).toEqual([
      { name: 'ok', path: 'live2d/models/ok/ok.model3.json' }
    ])
  })

  it('sorts results by name', async () => {
    await touch(join(modelsDir(), 'zeta', 'z.model3.json'))
    await touch(join(modelsDir(), 'alpha', 'a.model3.json'))
    await touch(join(modelsDir(), 'mid', 'm.model3.json'))
    const names = (await listModels(publicDir)).map((e) => e.name)
    expect(names).toEqual(['alpha', 'mid', 'zeta'])
  })
})

describe('importModel', () => {
  it('copies an external model directory including subdirectories', async () => {
    const src = join(extDir, 'Haru')
    await touch(join(src, 'Haru.model3.json'), '{"a":1}')
    await touch(join(src, 'motions', 'idle.motion3.json'), '{"m":1}')
    await touch(join(src, 'Haru.moc3'), 'moc')

    const entry = await importModel(join(src, 'Haru.model3.json'), publicDir)
    expect(entry).toEqual({ name: 'Haru', path: 'live2d/models/Haru/Haru.model3.json' })
    expect(await readFile(join(modelsDir(), 'Haru', 'motions', 'idle.motion3.json'), 'utf8')).toBe(
      '{"m":1}'
    )
    expect(await exists(join(modelsDir(), 'Haru', 'Haru.moc3'))).toBe(true)
    expect(await listModels(publicDir)).toEqual([entry])
    // 源目录保持不变
    expect(await exists(join(src, 'Haru.model3.json'))).toBe(true)
  })

  it('adds -2, -3 suffix when the target directory exists', async () => {
    const src = join(extDir, 'Haru')
    await touch(join(src, 'Haru.model3.json'))
    const e1 = await importModel(join(src, 'Haru.model3.json'), publicDir)
    const e2 = await importModel(join(src, 'Haru.model3.json'), publicDir)
    const e3 = await importModel(join(src, 'Haru.model3.json'), publicDir)
    expect(e1.path).toBe('live2d/models/Haru/Haru.model3.json')
    expect(e2).toEqual({ name: 'Haru-2', path: 'live2d/models/Haru-2/Haru.model3.json' })
    expect(e3).toEqual({ name: 'Haru-3', path: 'live2d/models/Haru-3/Haru.model3.json' })
  })

  it('replaces illegal characters (spaces) with underscore', async () => {
    const src = join(extDir, 'My Model (v2)')
    await touch(join(src, 'x.model3.json'))
    const entry = await importModel(join(src, 'x.model3.json'), publicDir)
    expect(entry).toEqual({
      name: 'My_Model__v2_',
      path: 'live2d/models/My_Model__v2_/x.model3.json'
    })
    expect(await exists(join(modelsDir(), 'My_Model__v2_', 'x.model3.json'))).toBe(true)
  })

  it('keeps Chinese directory names', async () => {
    const src = join(extDir, '小春')
    await touch(join(src, '小春.model3.json'))
    const entry = await importModel(join(src, '小春.model3.json'), publicDir)
    expect(entry).toEqual({ name: '小春', path: 'live2d/models/小春/小春.model3.json' })
    expect(await exists(join(modelsDir(), '小春', '小春.model3.json'))).toBe(true)
  })

  it('reports "dir/file" name when the imported directory has several models', async () => {
    const src = join(extDir, 'Pack')
    await touch(join(src, 'a.model3.json'))
    await touch(join(src, 'b.model3.json'))
    const entry = await importModel(join(src, 'b.model3.json'), publicDir)
    expect(entry).toEqual({ name: 'Pack/b', path: 'live2d/models/Pack/b.model3.json' })
    expect(await listModels(publicDir)).toContainEqual(entry)
  })

  it('does not copy a model that is already inside the models directory', async () => {
    await touch(join(modelsDir(), 'Hiyori', 'Hiyori.model3.json'))
    const entry = await importModel(join(modelsDir(), 'Hiyori', 'Hiyori.model3.json'), publicDir)
    expect(entry).toEqual({ name: 'Hiyori', path: 'live2d/models/Hiyori/Hiyori.model3.json' })
    expect(await listModels(publicDir)).toEqual([entry])
    expect(await exists(join(modelsDir(), 'Hiyori-2'))).toBe(false)
  })

  it('in-place entry matches listModels for multi-model directories', async () => {
    await touch(join(modelsDir(), 'Pack', 'a.model3.json'))
    await touch(join(modelsDir(), 'Pack', 'b.model3.json'))
    const entry = await importModel(join(modelsDir(), 'Pack', 'b.model3.json'), publicDir)
    expect(await listModels(publicDir)).toContainEqual(entry)
    expect(entry.name).toBe('Pack/b')
  })

  it('rejects files that do not end with .model3.json', async () => {
    const f = join(extDir, 'a.json')
    await touch(f)
    await expect(importModel(f, publicDir)).rejects.toThrow(Error)
  })

  it('rejects a non-existent file', async () => {
    await expect(importModel(join(extDir, 'nope', 'x.model3.json'), publicDir)).rejects.toThrow(
      '不存在'
    )
  })

  it('rejects a directory named like a model file', async () => {
    const d = join(extDir, 'fake.model3.json')
    await mkdir(d, { recursive: true })
    await expect(importModel(d, publicDir)).rejects.toThrow('不是文件')
  })

  it('creates the models directory if missing', async () => {
    const src = join(extDir, 'A')
    await touch(join(src, 'A.model3.json'))
    expect(await exists(modelsDir())).toBe(false)
    await importModel(join(src, 'A.model3.json'), publicDir)
    expect(await exists(modelsDir())).toBe(true)
  })
})

describe('importModel nested in models dir', () => {
  it('copies a deeper nested model into a new top-level directory', async () => {
    await touch(join(modelsDir(), 'outer', 'inner', 'n.model3.json'))
    const entry = await importModel(join(modelsDir(), 'outer', 'inner', 'n.model3.json'), publicDir)
    expect(entry).toEqual({ name: 'inner', path: 'live2d/models/inner/n.model3.json' })
  })
})

describe('importModel size guard', () => {
  it('refuses a source folder with too many files and copies nothing', async () => {
    const src = join(extDir, 'Desktop')
    await touch(join(src, 'x.model3.json'))
    for (let i = 0; i < 5; i++) await touch(join(src, `junk${i}.txt`), 'x')
    const saved = { ...IMPORT_LIMITS }
    IMPORT_LIMITS.maxFiles = 3
    try {
      await expect(importModel(join(src, 'x.model3.json'), publicDir)).rejects.toThrow(/过大/)
    } finally {
      Object.assign(IMPORT_LIMITS, saved)
    }
    expect(await exists(join(modelsDir(), 'Desktop'))).toBe(false)
  })
})

describe('completeModelSettings', () => {
  const vts = {
    Version: 3,
    FileReferences: { Moc: 'm.moc3', DisplayInfo: 'm.cdi3.json' },
    Groups: [
      { Target: 'Parameter', Name: 'EyeBlink', Ids: [] },
      { Target: 'Parameter', Name: 'LipSync', Ids: [] }
    ]
  }
  const allParams = new Set(['ParamEyeLOpen', 'ParamEyeROpen', 'ParamMouthOpenY'])

  it('fills empty EyeBlink/LipSync groups and registers unlisted expressions', () => {
    const out = completeModelSettings(vts, ['smile.exp3.json', 'exp/angry.exp3.json'], allParams)
    expect(out?.Groups).toEqual([
      { Target: 'Parameter', Name: 'EyeBlink', Ids: ['ParamEyeLOpen', 'ParamEyeROpen'] },
      { Target: 'Parameter', Name: 'LipSync', Ids: ['ParamMouthOpenY'] }
    ])
    expect((out?.FileReferences as Record<string, unknown>).Expressions).toEqual([
      { Name: 'angry', File: 'exp/angry.exp3.json' },
      { Name: 'smile', File: 'smile.exp3.json' }
    ])
    expect(vts.Groups[0].Ids).toEqual([])
  })

  it('returns null for an already complete model', () => {
    const done = completeModelSettings(vts, ['smile.exp3.json'], allParams)
    expect(completeModelSettings(done, ['smile.exp3.json'], allParams)).toBeNull()
  })

  it('keeps existing group ids and expression entries, de-duplicating names', () => {
    const settings = {
      FileReferences: { Expressions: [{ Name: 'smile', File: 'a/smile.exp3.json' }] },
      Groups: [{ Target: 'Parameter', Name: 'EyeBlink', Ids: ['Custom'] }]
    }
    const out = completeModelSettings(settings, ['A/Smile.exp3.json', 'b/smile.exp3.json'], allParams)
    expect(out?.Groups).toEqual([
      { Target: 'Parameter', Name: 'EyeBlink', Ids: ['Custom'] },
      { Target: 'Parameter', Name: 'LipSync', Ids: ['ParamMouthOpenY'] }
    ])
    expect((out?.FileReferences as Record<string, unknown>).Expressions).toEqual([
      { Name: 'smile', File: 'a/smile.exp3.json' },
      { Name: 'smile-2', File: 'b/smile.exp3.json' }
    ])
  })

  it('only uses parameters the model has; assumes standard ids when unknown', () => {
    const onlyLeft = completeModelSettings(vts, [], new Set(['ParamEyeLOpen']))
    expect(onlyLeft?.Groups).toEqual([
      { Target: 'Parameter', Name: 'EyeBlink', Ids: ['ParamEyeLOpen'] },
      { Target: 'Parameter', Name: 'LipSync', Ids: [] }
    ])
    expect(completeModelSettings({ FileReferences: {} }, [], null)?.Groups).toHaveLength(2)
    expect(completeModelSettings(vts, [], new Set())).toBeNull()
  })

  it('ignores input without FileReferences', () => {
    expect(completeModelSettings({}, ['x.exp3.json'], null)).toBeNull()
    expect(completeModelSettings('nope', [], null)).toBeNull()
  })
})

describe('importModel completes VTube Studio style models', () => {
  it('patches only the copy, using cdi3 parameters', async () => {
    const src = join(extDir, 'Vts')
    const original = JSON.stringify({
      FileReferences: { Moc: 'v.moc3', DisplayInfo: 'v.cdi3.json' },
      Groups: [{ Target: 'Parameter', Name: 'EyeBlink', Ids: [] }]
    })
    await touch(join(src, 'v.model3.json'), original)
    await touch(join(src, 'v.cdi3.json'), JSON.stringify({ Parameters: [{ Id: 'ParamEyeLOpen' }, { Id: 'ParamEyeROpen' }] }))
    await touch(join(src, 'wink.exp3.json'))

    await importModel(join(src, 'v.model3.json'), publicDir)
    const copied = JSON.parse(await readFile(join(modelsDir(), 'Vts', 'v.model3.json'), 'utf8'))
    expect(copied.Groups).toEqual([{ Target: 'Parameter', Name: 'EyeBlink', Ids: ['ParamEyeLOpen', 'ParamEyeROpen'] }])
    expect(copied.FileReferences.Expressions).toEqual([{ Name: 'wink', File: 'wink.exp3.json' }])
    expect(await readFile(join(src, 'v.model3.json'), 'utf8')).toBe(original)
  })

  it('leaves an unparseable model3.json untouched', async () => {
    const src = join(extDir, 'Bad')
    await touch(join(src, 'b.model3.json'), 'not json')
    await importModel(join(src, 'b.model3.json'), publicDir)
    expect(await readFile(join(modelsDir(), 'Bad', 'b.model3.json'), 'utf8')).toBe('not json')
  })
})
