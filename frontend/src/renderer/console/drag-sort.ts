import { onBeforeUnmount } from 'vue'

export interface DragSortOptions {
  /** Items of the list, inside the element `start` is bound to. */
  selector: string
  /** Only a press inside this part of an item starts a drag (default: anywhere on the item). */
  handle?: string
  /** Keys of the items, in the order they are rendered now. */
  keys: () => string[]
  /** Move the item at index `from` to index `to`; the list must re-render in the new order. */
  move: (from: number, to: number) => void
  /** The drag ended and the order changed. */
  done?: () => void
}

const HOLD_MS = 300
const SLOP_PX = 6
const NO_DRAG = 'input, textarea, button, a, [contenteditable], .n-switch, .n-base-selection, .n-tag__close'

/**
 * Long-press-and-drag reordering of a vertical list: bind `start` to the list's pointerdown.
 * While dragging, the pressed item has the class `drag-item` and <body> has `drag-sorting`.
 */
export function useDragSort(o: DragSortOptions): { start: (e: PointerEvent) => void } {
  let stop: (() => void) | null = null

  function start(e: PointerEvent): void {
    if (e.button !== 0 || stop) return
    const container = e.currentTarget as HTMLElement
    const target = e.target as Element
    if (target.closest(NO_DRAG) || (o.handle && !target.closest(o.handle))) return
    const items = (): HTMLElement[] => Array.from(container.querySelectorAll<HTMLElement>(o.selector))
    const item = target.closest<HTMLElement>(o.selector)
    const key = item ? o.keys()[items().indexOf(item)] : undefined
    if (key === undefined) return

    const x0 = e.clientX
    const y0 = e.clientY
    let active = false
    let moved = false
    const mark = (): void => items().forEach((el, i) => el.classList.toggle('drag-item', active && o.keys()[i] === key))
    const timer = window.setTimeout(() => {
      active = true
      document.body.classList.add('drag-sorting')
      mark()
    }, HOLD_MS)

    const onMove = (m: PointerEvent): void => {
      if (!active) {
        // Moved before the hold time: an ordinary drag (text selection, scrolling), not ours.
        if (Math.hypot(m.clientX - x0, m.clientY - y0) > SLOP_PX) stop?.()
        return
      }
      m.preventDefault()
      const from = o.keys().indexOf(key)
      const els = items()
      let to = from
      for (let i = 0; i < els.length; i++) {
        if (i === from) continue
        const r = els[i].getBoundingClientRect()
        const mid = r.top + r.height / 2
        if (i < from && m.clientY < mid) {
          to = i
          break
        }
        if (i > from && m.clientY > mid) to = i
      }
      if (from >= 0 && to !== from) {
        o.move(from, to)
        moved = true
      }
      // The framework may have rewritten class attributes while re-rendering.
      requestAnimationFrame(mark)
    }

    const swallowClick = (c: MouseEvent): void => {
      c.stopPropagation()
      c.preventDefault()
    }

    stop = () => {
      stop = null
      clearTimeout(timer)
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('pointercancel', onUp)
      if (!active) return
      active = false
      document.body.classList.remove('drag-sorting')
      mark()
      requestAnimationFrame(mark)
      // Releasing the button must not also click (open/navigate) the item.
      window.addEventListener('click', swallowClick, { capture: true, once: true })
      window.setTimeout(() => window.removeEventListener('click', swallowClick, { capture: true }), 50)
      if (moved) o.done?.()
    }
    const onUp = (): void => stop?.()

    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    window.addEventListener('pointercancel', onUp)
  }

  onBeforeUnmount(() => stop?.())
  return { start }
}
