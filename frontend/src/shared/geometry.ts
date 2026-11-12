export interface Point {
  x: number
  y: number
}
export interface Size {
  width: number
  height: number
}
export interface Rect {
  x: number
  y: number
  width: number
  height: number
}

/** 屏幕 DIP 坐标 -> 窗口客户区坐标（DIP）。结果可以在窗口外。 */
export function screenToWindow(cursor: Point, windowBounds: Rect): Point {
  return { x: cursor.x - windowBounds.x, y: cursor.y - windowBounds.y }
}

/** 拖拽：新窗口位置 = startWindow + (cursor - startCursor)，取整。 */
export function dragPosition(startWindow: Point, startCursor: Point, cursor: Point): Point {
  return {
    x: Math.round(startWindow.x + (cursor.x - startCursor.x)),
    y: Math.round(startWindow.y + (cursor.y - startCursor.y))
  }
}

function clampAxis(pos: number, size: number, areaPos: number, areaSize: number): number {
  if (size >= areaSize) return Math.round(areaPos)
  const min = areaPos
  const max = areaPos + areaSize - size
  return Math.round(Math.min(Math.max(pos, min), max))
}

/** 把窗口位置夹进工作区，保证窗口完全可见；窗口比工作区大时对齐左/上边。 */
export function clampToWorkArea(bounds: Rect, workArea: Rect): Point {
  return {
    x: clampAxis(bounds.x, bounds.width, workArea.x, workArea.width),
    y: clampAxis(bounds.y, bounds.height, workArea.y, workArea.height)
  }
}

/**
 * 拖拽用的夹取：纵向仍完全留在工作区内；横向可以拖出屏幕，只保证窗口至少还有 minVisible 像素在工作区里
 * （不会彻底丢失，托盘「重置位置」也能找回）。窗口比工作区宽时同样适用。
 */
export function clampDragToWorkArea(bounds: Rect, workArea: Rect, minVisible = 20): Point {
  const minX = workArea.x - bounds.width + minVisible
  const maxX = workArea.x + workArea.width - minVisible
  return {
    x: Math.round(Math.min(Math.max(bounds.x, minX), maxX)),
    y: clampAxis(bounds.y, bounds.height, workArea.y, workArea.height)
  }
}

/** 默认位置：工作区右下角，距右/下各 margin，再 clamp。 */
export function defaultWindowPosition(size: Size, workArea: Rect, margin = 0): Point {
  const x = workArea.x + workArea.width - size.width - margin
  const y = workArea.y + workArea.height - size.height - margin
  return clampToWorkArea({ x, y, width: size.width, height: size.height }, workArea)
}

/** 等比缩放模型放进画布：x 水平居中，y 底部对齐。 */
export function fitModel(
  model: Size,
  canvas: Size,
  padding = 0
): { scale: number; x: number; y: number } {
  if (!(model.width > 0) || !(model.height > 0)) {
    throw new RangeError(`invalid model size: ${model.width}x${model.height}`)
  }
  const scale = Math.min(
    (canvas.width - 2 * padding) / model.width,
    (canvas.height - 2 * padding) / model.height
  )
  const x = (canvas.width - model.width * scale) / 2
  const y = canvas.height - padding - model.height * scale
  return { scale, x, y }
}

export interface ModelPlacement {
  scale: number
  x: number
  y: number
  /** Region outside which the model must be clipped; null when nothing needs clipping. */
  clip: Rect | null
}

/**
 * Smallest window that shows a fixed-scale model uncropped (the bottom `chatRatio` strip belongs to the
 * chat bar). null for 'auto' scale, which always fits the window it is given.
 */
export function requiredWindowSize(
  model: Size,
  opts: { scale: number | 'auto'; framing: 'full' | 'half'; halfRatio: number },
  chatRatio: number,
  padding = 0
): Size | null {
  if (opts.scale === 'auto') return null
  const half = opts.framing === 'half'
  const visibleHeight = (half ? model.height * opts.halfRatio : model.height) * opts.scale
  const areaHeight = visibleHeight + (half ? padding : 2 * padding)
  return {
    width: Math.ceil(model.width * opts.scale + 2 * padding),
    height: Math.ceil(areaHeight / (1 - chatRatio))
  }
}

/** Half framing shows the top `halfRatio` of the model with the cut line on the area's bottom edge. */
export function placeModel(
  model: Size,
  area: Rect,
  opts: { scale: number | 'auto'; framing: 'full' | 'half'; halfRatio: number },
  padding = 0
): ModelPlacement {
  if (!(model.width > 0) || !(model.height > 0)) {
    throw new RangeError(`invalid model size: ${model.width}x${model.height}`)
  }
  const half = opts.framing === 'half'
  const visibleHeight = half ? model.height * opts.halfRatio : model.height
  const scale =
    opts.scale === 'auto'
      ? Math.min(
          (area.width - 2 * padding) / model.width,
          (area.height - (half ? padding : 2 * padding)) / visibleHeight
        )
      : opts.scale
  const bottom = area.y + area.height - (half ? 0 : padding)
  return {
    scale,
    x: area.x + (area.width - model.width * scale) / 2,
    y: bottom - visibleHeight * scale,
    clip: half ? { ...area } : null
  }
}
