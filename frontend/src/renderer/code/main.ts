import type { CodePayload } from '../../shared/ipc'

const title = document.getElementById('title') as HTMLSpanElement
const lang = document.getElementById('lang') as HTMLSpanElement
const code = document.getElementById('code') as HTMLPreElement
const empty = document.getElementById('empty') as HTMLDivElement
const copy = document.getElementById('copy') as HTMLButtonElement

function show(c: CodePayload | null): void {
  // textContent only: the code is model output and must never be parsed as HTML.
  title.textContent = c?.title ?? '代码'
  lang.textContent = c?.language ?? ''
  code.textContent = c?.code ?? ''
  empty.style.display = c ? 'none' : 'block'
  copy.disabled = !c
  document.title = c ? `代码 - ${c.title}` : '代码'
}

let resetTimer: number | undefined
copy.addEventListener('click', () => {
  void window.petCode.copy().then((ok) => {
    copy.textContent = ok ? '已复制 ✓' : '复制失败'
    copy.classList.toggle('ok', ok)
    window.clearTimeout(resetTimer)
    resetTimer = window.setTimeout(() => {
      copy.textContent = '复制'
      copy.classList.remove('ok')
    }, 1500)
  })
})

window.petCode.onShow(show)
void window.petCode.get().then(show)
