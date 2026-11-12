// Downloads the official Live2D sample model "Hiyori" (Live2D Free Material License); never committed.
import { mkdir, writeFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const NAME = 'Hiyori'
const BASE = `https://raw.githubusercontent.com/Live2D/CubismWebSamples/develop/Samples/Resources/${NAME}/`
const outDir = resolve(dirname(fileURLToPath(import.meta.url)), `../public/live2d/models/${NAME}`)

async function get(rel) {
  const res = await fetch(BASE + rel)
  if (!res.ok) throw new Error(`${rel}: ${res.status} ${res.statusText}`)
  return Buffer.from(await res.arrayBuffer())
}

async function save(rel, data) {
  const file = resolve(outDir, rel)
  await mkdir(dirname(file), { recursive: true })
  await writeFile(file, data)
  console.log(`  ${rel}`)
}

const manifestName = `${NAME}.model3.json`
const manifestBuf = await get(manifestName)
const refs = JSON.parse(manifestBuf.toString('utf8')).FileReferences
const files = [
  refs.Moc,
  ...(refs.Textures ?? []),
  refs.Physics,
  refs.Pose,
  refs.UserData,
  refs.DisplayInfo,
  ...(refs.Expressions ?? []).map((e) => e.File),
  ...Object.values(refs.Motions ?? {}).flat().map((m) => m.File)
].filter(Boolean)

console.log(`downloading ${NAME} -> ${outDir}`)
await save(manifestName, manifestBuf)
for (const rel of new Set(files)) await save(rel, await get(rel))
console.log(`done. model.path: "live2d/models/${NAME}/${manifestName}"`)
