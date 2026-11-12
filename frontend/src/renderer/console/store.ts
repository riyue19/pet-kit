import { ref } from 'vue'
import type { GlobalThemeOverrides } from 'naive-ui'
import type { UiSettings } from '../../shared/ipc'

export const ui = ref<UiSettings>({
  theme: 'auto',
  accent: '#8c6cff',
  fontSize: 14,
  consoleStyle: 'default',
  inputStyle: 'default',
  bubbleStyle: 'default',
  petCss: '',
  consoleCss: '',
  styles: [],
  navOrder: []
})

/** Key of the page shown in the console (App.vue's menu); pages set it to link to each other. */
export const activePage = ref('model')

/** Naive UI overrides for a console style preset; `dark` is the resolved theme. */
export function consoleStyleOverrides(style: string, dark: boolean): GlobalThemeOverrides {
  switch (style) {
    case 'round':
      return { common: { borderRadius: '12px' } }
    case 'sharp':
      return { common: { borderRadius: '2px' } }
    case 'warm': {
      const body = dark ? '#1d1b22' : '#f6f0e8'
      const card = dark ? '#27242d' : '#fffcf7'
      return {
        common: { borderRadius: '10px', bodyColor: body, cardColor: card, modalColor: card, popoverColor: card, tableColor: card },
        Layout: { color: body, siderColor: card }
      }
    }
    default:
      return {}
  }
}

function shade(hex: string, amount: number): string {
  const n = parseInt(hex.slice(1), 16)
  const ch = (shift: number): string => {
    const c = (n >> shift) & 0xff
    const v = amount >= 0 ? c + (255 - c) * amount : c * (1 + amount)
    return Math.round(v).toString(16).padStart(2, '0')
  }
  return `#${ch(16)}${ch(8)}${ch(0)}`
}

export function accentPalette(accent: string): Record<string, string> {
  return {
    primaryColor: accent,
    primaryColorHover: shade(accent, 0.15),
    primaryColorPressed: shade(accent, -0.15),
    primaryColorSuppl: shade(accent, 0.15)
  }
}

export function errorText(e: unknown): string {
  // Captured by the main process into the frontend log (with the stack when there is one).
  console.warn('[console]', e instanceof Error ? (e.stack ?? e.message) : String(e))
  const msg = e instanceof Error ? e.message : String(e)
  // ipcRenderer.invoke wraps main-process errors as "Error invoking remote method '...': Error: <msg>".
  return msg.replace(/^Error invoking remote method '[^']+': (?:\w*Error: )?/, '')
}
