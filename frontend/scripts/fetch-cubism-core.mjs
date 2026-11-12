// Cubism Core is under the Live2D Proprietary Software License; fetched locally, never committed.
import { mkdir, writeFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const URL = 'https://cubism.live2d.com/sdk-web/cubismcore/live2dcubismcore.min.js'
const out = resolve(dirname(fileURLToPath(import.meta.url)), '../public/live2d/live2dcubismcore.min.js')

const res = await fetch(URL)
if (!res.ok) throw new Error(`download failed: ${res.status} ${res.statusText}`)
await mkdir(dirname(out), { recursive: true })
await writeFile(out, Buffer.from(await res.arrayBuffer()))
console.log(`saved ${out}`)
