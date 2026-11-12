/** Style presets shared by the main process (validation), the pet window and the console. */

export interface StylePreset {
  id: string
  label: string
}

export const CONSOLE_STYLES: readonly StylePreset[] = [
  { id: 'default', label: '默认' },
  { id: 'round', label: '圆润' },
  { id: 'sharp', label: '方正' },
  { id: 'warm', label: '暖色' }
]

export const INPUT_STYLES: readonly StylePreset[] = [
  { id: 'default', label: '深色胶囊' },
  { id: 'light', label: '浅色' },
  { id: 'glass', label: '毛玻璃' },
  { id: 'outline', label: '描边' },
  { id: 'pixel', label: '像素方框' }
]

export const BUBBLE_STYLES: readonly StylePreset[] = [
  { id: 'default', label: '白色圆角' },
  { id: 'dark', label: '深色' },
  { id: 'pink', label: '粉色可爱' },
  { id: 'glass', label: '毛玻璃' },
  { id: 'pixel', label: '像素方框' }
]

export const CUSTOM_CSS_MAX_CHARS = 20000

/** The part of the UI settings the pet window applies. */
export interface PetStyle {
  inputStyle: string
  bubbleStyle: string
  petCss: string
}

/** Everything a saved style scheme captures: pet look + console look. */
export interface StyleFields extends PetStyle {
  consoleStyle: string
  consoleCss: string
}

/** A named snapshot of StyleFields the user can switch back to. */
export interface StyleScheme extends StyleFields {
  name: string
}

/** CSS the pet itself proposes (set_style). Mirrors backend/action/builtin.py. */
export const AI_CSS_MAX_CHARS = 4000
const BANNED_AI_CSS = /@import|url\s*\(|<\/|<!--|expression\s*\(|javascript:|behavior\s*:/i
export const isSafeAiCss = (css: unknown): css is string =>
  typeof css === 'string' && css.length <= AI_CSS_MAX_CHARS && !BANNED_AI_CSS.test(css)

export const MAX_SCHEMES = 30
export const MAX_SCHEME_NAME_CHARS = 30

export const isPreset = (list: readonly StylePreset[], id: unknown): id is string =>
  typeof id === 'string' && list.some((p) => p.id === id)
