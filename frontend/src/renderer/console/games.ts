import { computed, reactive } from 'vue'
import { useMessage } from 'naive-ui'
import type { BackendMethod, GameKey, GameSide, GamesState, Gesture, RouletteItem, RouletteLog } from '../../shared/backend'
import { errorText } from './store'

interface Store {
  loading: boolean
  loaded: boolean
  offline: boolean
  error: string | null
  state: GamesState | null
  /** A request is in flight; only one at a time. */
  busy: boolean
}

export const games = reactive<Store>({
  loading: false,
  loaded: false,
  offline: false,
  error: null,
  state: null,
  busy: false
})

export async function loadGames(): Promise<void> {
  games.loading = true
  try {
    const res = await window.petConsole.backend<GamesState>('GET', '/api/games')
    games.offline = !res.ok && res.offline
    games.error = res.ok ? null : res.error
    if (res.ok) {
      games.state = res.data
      games.loaded = true
    }
  } finally {
    games.loading = false
  }
}

/** Sends a game action; stores and returns the new state. Throws a user-facing message on failure. */
export async function gamePost(path: string, body?: unknown, method: BackendMethod = 'POST'): Promise<GamesState> {
  games.busy = true
  try {
    const res = await window.petConsole.backend<GamesState>(method, path, body)
    if (!res.ok) throw new Error(res.error || '操作失败')
    games.state = res.data
    return res.data
  } finally {
    games.busy = false
  }
}

/**
 * For components: `const run = useGameRun()`, then `await run('/api/games/…', body)`.
 * One request at a time; a failure is shown as a message and returns false.
 */
export function useGameRun(): (path: string, body?: unknown, method?: BackendMethod) => Promise<boolean> {
  const message = useMessage()
  return async (path, body, method = 'POST') => {
    if (games.busy) return false
    try {
      await gamePost(path, body, method)
      return true
    } catch (e) {
      message.error(errorText(e), { duration: 6000 })
      return false
    }
  }
}

/** The pet's name from the persona (config/persona.yaml). */
export const petName = computed(() => games.state?.pet_name || '桌宠')
/** "你" or the pet's name. */
export const who = (side: GameSide): string => (side === 'player' ? '你' : petName.value)

export const GAME_KEYS: GameKey[] = ['roulette', 'rps', 'dice', 'auction', 'taboo']
export const GAME_INFO: Record<GameKey, { name: string; desc: string }> = {
  roulette: { name: '恶魔轮盘赌', desc: '真弹假弹，朝谁开枪' },
  rps: { name: '骗子猜拳', desc: '亮两个手势，暗出一个' },
  dice: { name: '吹牛骰', desc: '报数、加码，还是喊开' },
  auction: { name: '拍卖数字牌', desc: '暗中出价，先到 45 分' },
  taboo: { name: '禁忌词', desc: '不说那个词，让对方猜到' }
}

export const ITEM_INFO: Record<RouletteItem, { name: string; desc: string }> = {
  juice: { name: '果汁', desc: '回复 1 点生命（满血不能用）' },
  dice: { name: '骰子', desc: '猜大小：1~3 小、4~6 大，猜对回 1 点生命，猜错扣 1 点' },
  flash: { name: '震爆弹', desc: '下一枪如果是真弹，伤害变成 2' },
  lens: { name: '放大镜', desc: '偷看当前这一发是真弹还是假弹（只有你知道）' },
  driver: { name: '螺丝刀', desc: '卸掉当前这一发子弹' }
}

export const GESTURE_NAME: Record<Gesture, string> = { rock: '石头', scissors: '剪刀', paper: '布' }

/** A card's face: 1 -> A, 11 -> J, 12 -> Q, 13 -> K. */
export const cardFace = (n: number): string => ({ 1: 'A', 11: 'J', 12: 'Q', 13: 'K' })[n] ?? String(n)

/** Description of one public roulette log entry. */
export function logText(e: RouletteLog): string {
  const name = who(e.side)
  switch (e.kind) {
    case 'load':
      return `第 ${e.round} 轮：真弹 ${e.live} 发，假弹 ${e.blank} 发；道具重新发放，${name}先手`
    case 'shoot':
      return `${name}朝${e.target === 'self' ? '自己' : '对方'}开枪：${e.live ? `真弹，-${e.damage}` : '假弹'}`
    case 'item': {
      let s = `${name}用了${e.item ? ITEM_INFO[e.item].name : '道具'}`
      if (e.item === 'dice') s += `：猜${e.guess === 'big' ? '大' : '小'}，掷出 ${e.roll}，${e.ok ? '猜对 +1' : '猜错 -1'}`
      if (e.item === 'driver') s += `：卸掉一发${e.live ? '真弹' : '假弹'}`
      return s
    }
    case 'win':
      return `${name}赢了`
    default:
      return ''
  }
}
