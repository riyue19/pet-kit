import { mkdtemp, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { instanceName } from '../../frontend/src/main/backend'

async function dirWith(text: string | null): Promise<string> {
  const dir = await mkdtemp(join(tmpdir(), 'pet-kit-inst-'))
  if (text !== null) await writeFile(join(dir, 'instance.yaml'), text, 'utf8')
  return dir
}

describe('instanceName', () => {
  it('defaults to pet-kit without the file', async () => {
    expect(instanceName(await dirWith(null))).toBe('pet-kit')
  })
  it('reads the name', async () => {
    expect(instanceName(await dirWith('name: pet-test\n'))).toBe('pet-test')
  })
  it('cleans the name like the backend does', async () => {
    expect(instanceName(await dirWith("name: '../测试 copy/'\n"))).toBe('copy')
    expect(instanceName(await dirWith("name: ''\n"))).toBe('pet-kit')
    expect(instanceName(await dirWith('name: 123\n'))).toBe('pet-kit')
  })
})
