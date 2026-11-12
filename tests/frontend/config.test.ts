import { describe, expect, it } from 'vitest'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import {
  ConfigError,
  DEFAULT_CONFIG,
  loadFrontendConfig,
  parseFrontendConfig,
  updateModelConfig
} from '../../frontend/src/main/config'
import { mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises'

function expectError(yaml: string, pathFragment: string): void {
  let err: unknown
  try {
    parseFrontendConfig(yaml)
  } catch (e) {
    err = e
  }
  expect(err).toBeInstanceOf(ConfigError)
  expect((err as ConfigError).message).toContain(pathFragment)
}

describe('parseFrontendConfig defaults', () => {
  it('empty text yields defaults', () => {
    expect(parseFrontendConfig('')).toEqual(DEFAULT_CONFIG)
  })

  it('comment-only text yields defaults', () => {
    expect(parseFrontendConfig('# nothing here\n')).toEqual(DEFAULT_CONFIG)
  })

  it('missing sections yield defaults; unknown keys ignored', () => {
    expect(parseFrontendConfig('foo: 1\nmodel:\n  bar: 2\n')).toEqual(DEFAULT_CONFIG)
  })

  it('merges partial fields', () => {
    const cfg = parseFrontendConfig(
      'model:\n  path: live2d/models/a/a.model3.json\nwindow:\n  width: 500\n  x: -100\neye_tracking:\n  fps: 60\n'
    )
    expect(cfg.model.path).toBe('live2d/models/a/a.model3.json')
    expect(cfg.model.scale).toBe('auto')
    expect(cfg.window).toEqual({ ...DEFAULT_CONFIG.window, width: 500, x: -100 })
    expect(cfg.eyeTracking.fps).toBe(60)
    expect(cfg.hitTest).toEqual(DEFAULT_CONFIG.hitTest)
  })

  it('maps all snake_case keys', () => {
    const cfg = parseFrontendConfig(
      [
        'model: { path: "m/x.model3.json", scale: 1.5, framing: half, half_ratio: 0.6 }',
        'window: { width: 300, height: 700, x: 10, y: null, always_on_top: false }',
        'eye_tracking: { fps: 15 }',
        'hit_test: { alpha_threshold: 0 }'
      ].join('\n')
    )
    expect(cfg).toEqual({
      model: { path: 'm/x.model3.json', scale: 1.5, framing: 'half', halfRatio: 0.6 },
      window: { width: 300, height: 700, x: 10, y: null, alwaysOnTop: false },
      eyeTracking: { fps: 15 },
      hitTest: { alphaThreshold: 0 }
    })
  })

  it('does not share references with DEFAULT_CONFIG', () => {
    const cfg = parseFrontendConfig('')
    expect(cfg).not.toBe(DEFAULT_CONFIG)
    expect(cfg.model).not.toBe(DEFAULT_CONFIG.model)
    expect(cfg.window).not.toBe(DEFAULT_CONFIG.window)
    expect(cfg.eyeTracking).not.toBe(DEFAULT_CONFIG.eyeTracking)
    expect(cfg.hitTest).not.toBe(DEFAULT_CONFIG.hitTest)
    cfg.window.width = 999
    expect(DEFAULT_CONFIG.window.width).toBe(400)
  })

  it('default values match spec', () => {
    expect(DEFAULT_CONFIG).toEqual({
      model: { path: '', scale: 'auto', framing: 'full', halfRatio: 0.55 },
      window: { width: 400, height: 600, x: null, y: null, alwaysOnTop: true },
      eyeTracking: { fps: 30 },
      hitTest: { alphaThreshold: 10 }
    })
  })
})

describe('parseFrontendConfig structure errors', () => {
  it('YAML syntax error is wrapped', () => {
    expect(() => parseFrontendConfig('model: [unclosed')).toThrow(ConfigError)
    expect(() => parseFrontendConfig('a: b: c: [')).toThrow(ConfigError)
  })

  it('top-level not an object', () => {
    expect(() => parseFrontendConfig('- 1\n- 2\n')).toThrow(ConfigError)
    expect(() => parseFrontendConfig('42')).toThrow(ConfigError)
  })

  it('section not an object', () => {
    expectError('model: 3', 'model')
    expectError('window: [1]', 'window')
    expectError('eye_tracking: hello', 'eye_tracking')
    expectError('hit_test: true', 'hit_test')
  })
})

describe('parseFrontendConfig validation', () => {
  it('model.path must be a string', () => expectError('model: { path: 5 }', 'model.path'))
  it('model.path must end with .model3.json', () =>
    expectError('model: { path: "a/b.json" }', 'model.path'))
  it('model.path rejects leading slash', () =>
    expectError('model: { path: "/a/b.model3.json" }', 'model.path'))
  it('model.path rejects leading backslash', () =>
    expectError('model: { path: "\\\\a\\\\b.model3.json" }', 'model.path'))
  it('model.path rejects drive letter', () =>
    expectError('model: { path: "C:/a/b.model3.json" }', 'model.path'))
  it('model.path rejects .. segments', () => {
    expectError('model: { path: "a/../b.model3.json" }', 'model.path')
    expectError('model: { path: "a\\\\..\\\\b.model3.json" }', 'model.path')
  })
  it('model.path allows empty string', () => {
    expect(parseFrontendConfig('model: { path: "" }').model.path).toBe('')
  })

  it('model.scale rejects bad values', () => {
    expectError('model: { scale: "big" }', 'model.scale')
    expectError('model: { scale: 0 }', 'model.scale')
    expectError('model: { scale: -1 }', 'model.scale')
    expectError('model: { scale: 10.5 }', 'model.scale')
    expectError('model: { scale: .inf }', 'model.scale')
  })
  it('model.scale accepts boundary 10', () => {
    expect(parseFrontendConfig('model: { scale: 10 }').model.scale).toBe(10)
  })

  it('validates framing and half_ratio', () => {
    expectError('model: { framing: upper }', 'model.framing')
    expectError('model: { half_ratio: 0.1 }', 'model.half_ratio')
    expectError('model: { half_ratio: 1.5 }', 'model.half_ratio')
    expectError('model: { half_ratio: "x" }', 'model.half_ratio')
    expect(parseFrontendConfig('model: { half_ratio: 0.2 }').model.halfRatio).toBe(0.2)
    expect(parseFrontendConfig('model: { half_ratio: 1 }').model.halfRatio).toBe(1)
  })

  it('window.width / height range and integer', () => {
    expectError('window: { width: 99 }', 'window.width')
    expectError('window: { width: 4001 }', 'window.width')
    expectError('window: { width: 400.5 }', 'window.width')
    expectError('window: { width: "400" }', 'window.width')
    expectError('window: { height: 99 }', 'window.height')
    expectError('window: { height: 5000 }', 'window.height')
  })

  it('window.x / y must be integer or null', () => {
    expectError('window: { x: 1.5 }', 'window.x')
    expectError('window: { x: "a" }', 'window.x')
    expectError('window: { y: true }', 'window.y')
    expect(parseFrontendConfig('window: { x: -1920, y: -5 }').window).toMatchObject({
      x: -1920,
      y: -5
    })
  })

  it('window.always_on_top must be boolean', () =>
    expectError('window: { always_on_top: "yes" }', 'window.always_on_top'))

  it('eye_tracking.fps range', () => {
    expectError('eye_tracking: { fps: 0 }', 'eye_tracking.fps')
    expectError('eye_tracking: { fps: 61 }', 'eye_tracking.fps')
    expectError('eye_tracking: { fps: 30.5 }', 'eye_tracking.fps')
  })

  it('hit_test.alpha_threshold range', () => {
    expectError('hit_test: { alpha_threshold: -1 }', 'hit_test.alpha_threshold')
    expectError('hit_test: { alpha_threshold: 256 }', 'hit_test.alpha_threshold')
    expectError('hit_test: { alpha_threshold: 1.5 }', 'hit_test.alpha_threshold')
  })
})

describe('loadFrontendConfig', () => {
  it('returns a deep copy of defaults when file is missing', async () => {
    const p = join(tmpdir(), `pet-kit-nonexistent-${Date.now()}-${Math.random()}`, 'frontend.yaml')
    const cfg = await loadFrontendConfig(p)
    expect(cfg).toEqual(DEFAULT_CONFIG)
    expect(cfg).not.toBe(DEFAULT_CONFIG)
    expect(cfg.window).not.toBe(DEFAULT_CONFIG.window)
  })

  it('parses an existing file', async () => {
    const { mkdtemp, writeFile, rm } = await import('node:fs/promises')
    const dir = await mkdtemp(join(tmpdir(), 'pet-kit-cfg-'))
    try {
      const p = join(dir, 'frontend.yaml')
      await writeFile(p, 'window:\n  width: 321\n', 'utf8')
      const cfg = await loadFrontendConfig(p)
      expect(cfg.window.width).toBe(321)
    } finally {
      await rm(dir, { recursive: true, force: true })
    }
  })

  it('rethrows non-ENOENT errors (directory path)', async () => {
    await expect(loadFrontendConfig(tmpdir())).rejects.toThrow()
  })
})

describe('updateModelConfig', () => {
  async function withDir(fn: (dir: string) => Promise<void>): Promise<void> {
    const dir = await mkdtemp(join(tmpdir(), 'pet-kit-upd-'))
    try {
      await fn(dir)
    } finally {
      await rm(dir, { recursive: true, force: true })
    }
  }

  it('preserves comments and other fields', async () => {
    await withDir(async (dir) => {
      const p = join(dir, 'frontend.yaml')
      await writeFile(
        p,
        [
          '# top comment',
          'model:',
          '  path: ""  # inline model comment',
          '  scale: auto',
          'window:',
          '  # window size comment',
          '  width: 500',
          ''
        ].join('\n'),
        'utf8'
      )
      const cfg = await updateModelConfig(p, { path: 'live2d/models/a/a.model3.json', scale: 1.5, framing: 'full', halfRatio: 0.55 })
      expect(cfg.model).toEqual({ path: 'live2d/models/a/a.model3.json', scale: 1.5, framing: 'full', halfRatio: 0.55 })
      expect(cfg.window.width).toBe(500)

      const text = await readFile(p, 'utf8')
      expect(text).toContain('# top comment')
      expect(text).toContain('# window size comment')
      expect(text).toContain('# inline model comment')
      const reloaded = await loadFrontendConfig(p)
      expect(reloaded).toEqual(cfg)
      expect(reloaded.window.width).toBe(500)
    })
  })

  it('creates the file when missing', async () => {
    await withDir(async (dir) => {
      const p = join(dir, 'frontend.yaml')
      const cfg = await updateModelConfig(p, { path: 'live2d/models/a/a.model3.json', scale: 'auto', framing: 'full', halfRatio: 0.55 })
      expect(cfg.model).toEqual({ path: 'live2d/models/a/a.model3.json', scale: 'auto', framing: 'full', halfRatio: 0.55 })
      expect(await loadFrontendConfig(p)).toEqual(cfg)
    })
  })

  it('works on an empty or comment-only file', async () => {
    await withDir(async (dir) => {
      const p = join(dir, 'frontend.yaml')
      await writeFile(p, '# only a comment\n', 'utf8')
      await updateModelConfig(p, { path: 'live2d/models/a/a.model3.json', scale: 2, framing: 'full', halfRatio: 0.55 })
      const text = await readFile(p, 'utf8')
      expect(text).toContain('# only a comment')
      expect((await loadFrontendConfig(p)).model.scale).toBe(2)
    })
  })

  it('rejects invalid path with ConfigError and leaves the file untouched', async () => {
    await withDir(async (dir) => {
      const p = join(dir, 'frontend.yaml')
      const original = '# keep me\nwindow:\n  width: 500\n'
      await writeFile(p, original, 'utf8')
      await expect(
        updateModelConfig(p, { path: '../x.model3.json', scale: 'auto', framing: 'full', halfRatio: 0.55 })
      ).rejects.toBeInstanceOf(ConfigError)
      expect(await readFile(p, 'utf8')).toBe(original)
      expect(await readdir(dir)).toEqual(['frontend.yaml'])
    })
  })

  it('rejects invalid scale with ConfigError', async () => {
    await withDir(async (dir) => {
      const p = join(dir, 'frontend.yaml')
      await expect(
        updateModelConfig(p, { path: 'a/a.model3.json', scale: 0, framing: 'full', halfRatio: 0.55 })
      ).rejects.toBeInstanceOf(ConfigError)
      expect(await readdir(dir)).toEqual([])
    })
  })

  it('throws ConfigError on YAML syntax errors without writing', async () => {
    await withDir(async (dir) => {
      const p = join(dir, 'frontend.yaml')
      const original = 'model: [unclosed\n'
      await writeFile(p, original, 'utf8')
      await expect(
        updateModelConfig(p, { path: 'a/a.model3.json', scale: 'auto', framing: 'full', halfRatio: 0.55 })
      ).rejects.toBeInstanceOf(ConfigError)
      expect(await readFile(p, 'utf8')).toBe(original)
      expect(await readdir(dir)).toEqual(['frontend.yaml'])
    })
  })

  it('writes numeric scale and "auto" and reads them back', async () => {
    await withDir(async (dir) => {
      const p = join(dir, 'frontend.yaml')
      await updateModelConfig(p, { path: 'a/a.model3.json', scale: 0.75, framing: 'full', halfRatio: 0.55 })
      expect((await loadFrontendConfig(p)).model.scale).toBe(0.75)
      await updateModelConfig(p, { path: 'a/a.model3.json', scale: 'auto', framing: 'full', halfRatio: 0.55 })
      expect((await loadFrontendConfig(p)).model.scale).toBe('auto')
      await updateModelConfig(p, { path: 'b/b.model3.json', scale: 3, framing: 'full', halfRatio: 0.55 })
      const cfg = await loadFrontendConfig(p)
      expect(cfg.model).toEqual({ path: 'b/b.model3.json', scale: 3, framing: 'full', halfRatio: 0.55 })
    })
  })

  it('leaves no .tmp file behind', async () => {
    await withDir(async (dir) => {
      const p = join(dir, 'frontend.yaml')
      await updateModelConfig(p, { path: 'a/a.model3.json', scale: 1, framing: 'full', halfRatio: 0.55 })
      expect(await readdir(dir)).toEqual(['frontend.yaml'])
    })
  })
})
