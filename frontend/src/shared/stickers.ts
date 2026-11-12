/** Stickers (表情包): user-imported pictures the LLM may attach to a reply by their note name. */

export const STICKER_NAME_MAX = 20
export const MAX_STICKERS = 60
export const STICKER_MAX_BYTES = 2 * 1024 * 1024
export const STICKER_EXTENSIONS = ['png', 'jpg', 'jpeg', 'gif', 'webp'] as const

/** One entry of config/stickers.yaml; the picture is data/stickers/<file>. */
export interface Sticker {
  id: string
  file: string
  /** Note name: sent to the LLM, and what the LLM answers with to send this picture. */
  name: string
  enabled: boolean
}

/** A sticker as the console shows it. */
export interface StickerEntry {
  id: string
  name: string
  enabled: boolean
  /** Small preview as a data URL; '' when the picture file is missing or unreadable. */
  thumb: string
}

export interface StickerImportResult {
  stickers: StickerEntry[]
  /** One readable line per file that was not imported. */
  errors: string[]
}

export interface StickerPatch {
  name?: string
  enabled?: boolean
}

/** Characters a note name may not contain (they would break the JSON reply format or the prompt line). */
export const STICKER_NAME_FORBIDDEN = /[\u0000-\u001f\u007f"'\\{}[\]]/

/** Why `name` cannot be used as a note name (null = fine). `others` = the names of the other stickers. */
export function stickerNameError(name: string, others: readonly string[] = []): string | null {
  const n = name.trim()
  if (!n) return '备注名不能为空'
  if ([...n].length > STICKER_NAME_MAX) return `备注名最长 ${STICKER_NAME_MAX} 个字`
  if (STICKER_NAME_FORBIDDEN.test(n)) return '备注名不能包含引号、反斜杠、括号 {} [] 或换行'
  if (others.includes(n)) return `已经有叫「${n}」的表情包了`
  return null
}
