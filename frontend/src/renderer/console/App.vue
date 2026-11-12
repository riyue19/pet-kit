<script setup lang="ts">
import { computed, h, onMounted, ref, watchEffect, type Component } from 'vue'
import {
  darkTheme,
  NConfigProvider,
  NGlobalStyle,
  NLayout,
  NLayoutContent,
  NLayoutSider,
  NMenu,
  NMessageProvider,
  useOsTheme,
  type GlobalThemeOverrides,
  type MenuOption
} from 'naive-ui'
import { accentPalette, activePage, consoleStyleOverrides, ui } from './store'
import { useDragSort } from './drag-sort'
import { loadGames } from './games'
import AllOffButton from './components/AllOffButton.vue'
import BackendStatus from './components/BackendStatus.vue'
import StickersPage from './pages/StickersPage.vue'
import GamesPage from './pages/GamesPage.vue'
import ModelPage from './pages/ModelPage.vue'
import UiPage from './pages/UiPage.vue'
import VoicePage from './pages/VoicePage.vue'
import PersonaPage from './pages/PersonaPage.vue'
import PromptPage from './pages/PromptPage.vue'
import EventsPage from './pages/EventsPage.vue'
import LogsPage from './pages/LogsPage.vue'
import HistoryPage from './pages/HistoryPage.vue'
import MemoryPage from './pages/MemoryPage.vue'
import HotkeysPage from './pages/HotkeysPage.vue'
import ScreenPage from './pages/ScreenPage.vue'
import LlmPage from './pages/LlmPage.vue'
import VisionPage from './pages/VisionPage.vue'
import SkillsPage from './pages/SkillsPage.vue'
import StatusPage from './pages/StatusPage.vue'

type PageKey = 'model' | 'persona' | 'status' | 'prompt' | 'stickers' | 'games' | 'llm' | 'vision' | 'voice' | 'history' | 'memory' | 'hotkeys' | 'screen' | 'skills' | 'events' | 'logs' | 'ui'

const icon = (d: string) => () =>
  h('svg', { viewBox: '0 0 24 24', width: '1em', height: '1em', fill: 'none', stroke: 'currentColor', 'stroke-width': 1.8, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, [
    h('path', { d })
  ])

const allMenu: MenuOption[] = [
  { key: 'model', label: '形象', icon: icon('M12 3a4 4 0 1 1 0 8a4 4 0 0 1 0-8zM4 21c0-4 3.6-7 8-7s8 3 8 7') },
  { key: 'persona', label: '人设', icon: icon('M12 3l2.6 5.4 5.9.8-4.3 4.1 1 5.9L12 16.4 6.8 19.2l1-5.9L3.5 9.2l5.9-.8z') },
  { key: 'status', label: '状态', icon: icon('M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z') },
  { key: 'prompt', label: '提示词', icon: icon('M6 3h9l4 4v14H6zM14 3v5h5M9 13h7M9 17h4') },
  { key: 'stickers', label: '表情包', icon: icon('M12 3a9 9 0 1 0 0 18a9 9 0 0 0 0-18zM8.5 14.5a4 4 0 0 0 7 0M9 9.5h.01M15 9.5h.01') },
  { key: 'llm', label: 'AI 模型', icon: icon('M4 5h16v11H8l-4 4zM8 10h8M8 13h5') },
  { key: 'vision', label: '识图', icon: icon('M2 12s3.6-7 10-7s10 7 10 7s-3.6 7-10 7S2 12 2 12zM12 9a3 3 0 1 1 0 6a3 3 0 0 1 0-6z') },
  { key: 'voice', label: '语音', icon: icon('M12 3a3 3 0 0 1 3 3v6a3 3 0 0 1-6 0V6a3 3 0 0 1 3-3zM5 11a7 7 0 0 0 14 0M12 18v3') },
  { key: 'history', label: '聊天记录', icon: icon('M4 5h16v11H8l-4 4zM8 9h8M8 12h6') },
  { key: 'memory', label: '记忆', icon: icon('M12 4a5 5 0 0 0-5 5c0 2 1 3 2 4v3h6v-3c1-1 2-2 2-4a5 5 0 0 0-5-5zM10 20h4') },
  { key: 'screen', label: '感知与主动', icon: icon('M3 4h18v12H3zM8 20h8M12 16v4') },
  { key: 'hotkeys', label: '快捷键', icon: icon('M3 7h18v10H3zM7 11h.01M11 11h.01M15 11h.01M7 14h10') },
  { key: 'skills', label: '指令', icon: icon('M4 17l6-6-6-6M12 19h8') },
  { key: 'games', label: '小游戏', icon: icon('M7 8h10a4 4 0 0 1 4 4v2a3 3 0 0 1-5.4 1.8L15 15H9l-.6.8A3 3 0 0 1 3 14v-2a4 4 0 0 1 4-4zM8 11v3M6.5 12.5h3M15.5 11.5h.01M17.5 13.5h.01') },
  { key: 'events', label: '事件', icon: icon('M13 2L4 14h7l-1 8 9-12h-7z') },
  { key: 'logs', label: '日志', icon: icon('M6 3h9l4 4v14H6zM14 3v5h5M9 12h7M9 16h7') },
  { key: 'ui', label: '界面', icon: icon('M12 3a9 9 0 1 0 0 18c1 0 1.5-.8 1.5-1.5c0-1.2-1-1.5-1-2.5s.8-1.5 2-1.5H17a4 4 0 0 0 4-4c0-4.7-4-8.5-9-8.5zM7.5 11.5h.01M10 7.5h.01M15 7.5h.01') }
]

const pages: Record<PageKey, { component: Component; props?: Record<string, string> }> = {
  model: { component: ModelPage },
  llm: { component: LlmPage },
  vision: { component: VisionPage },
  voice: { component: VoicePage },
  persona: { component: PersonaPage },
  prompt: { component: PromptPage },
  stickers: { component: StickersPage },
  games: { component: GamesPage },
  status: { component: StatusPage },
  events: { component: EventsPage },
  logs: { component: LogsPage },
  history: { component: HistoryPage },
  memory: { component: MemoryPage },
  hotkeys: { component: HotkeysPage },
  screen: { component: ScreenPage },
  skills: { component: SkillsPage },
  ui: { component: UiPage }
}

// Pages link to each other through the store (e.g. 提示词 -> 人设).
const active = computed<PageKey>({
  get: () => (activePage.value in pages ? (activePage.value as PageKey) : 'model'),
  set: (k) => (activePage.value = k)
})

// The side bar is grouped; which group a page belongs to is fixed here.
const GROUPS: { key: string; label: string; pages: PageKey[] }[] = [
  { key: 'pet', label: '角色', pages: ['model', 'persona', 'status', 'prompt', 'stickers'] },
  { key: 'talk', label: '对话', pages: ['llm', 'vision', 'voice', 'history', 'memory'] },
  { key: 'sense', label: '感知与操作', pages: ['screen', 'hotkeys', 'skills'] },
  { key: 'fun', label: '娱乐', pages: ['games'] },
  { key: 'system', label: '系统', pages: ['ui', 'events', 'logs'] }
]
const groupOf = (page: string): string | undefined => GROUPS.find((g) => (g.pages as string[]).includes(page))?.key

// Within a group the pages follow the user's order (config/console.yaml nav_order); pages it doesn't list
// follow in the built-in order. Dragging only reorders inside a group.
const navKeys = computed<string[]>(() => {
  const order = ui.value.navOrder
  const rank = (k: string, builtin: number): number => (order.includes(k) ? order.indexOf(k) : order.length + builtin)
  return GROUPS.flatMap((g) => g.pages.map((k, i) => ({ k, r: rank(k, i) })).sort((a, b) => a.r - b.r).map((x) => x.k))
})
const menu = computed<MenuOption[]>(() => {
  const byKey = new Map(allMenu.map((m) => [String(m.key), m]))
  return GROUPS.map((g) => ({
    type: 'group',
    key: `group-${g.key}`,
    label: g.label,
    children: navKeys.value.filter((k) => groupOf(k) === g.key).map((k) => byKey.get(k)).filter((m): m is MenuOption => !!m)
  }))
})
const navDrag = useDragSort({
  selector: '.n-menu-item',
  keys: () => navKeys.value,
  move: (from, to) => {
    const keys = [...navKeys.value]
    if (groupOf(keys[from]) !== groupOf(keys[to])) return
    const [k] = keys.splice(from, 1)
    keys.splice(to, 0, k)
    ui.value = { ...ui.value, navOrder: keys }
  },
  done: () => void saveNavOrder()
})
// Saved at once and on its own: re-read the file so unsaved edits on the 界面 page are not written with it.
async function saveNavOrder(): Promise<void> {
  try {
    const stored = await window.petConsole.getUi()
    await window.petConsole.saveUi({ ...stored, navOrder: [...ui.value.navOrder] })
  } catch (e) {
    console.warn('[console] 保存菜单顺序失败', e)
  }
}

// After 关闭全部感知: pages re-read their settings when opened again, so reopen the current one to show the new state.
function reloadCurrent(): void {
  const cur = active.value
  active.value = 'ui'
  setTimeout(() => (active.value = cur))
}
const current = computed(() => pages[active.value])
// Log and event lists need the room; form pages stay at a readable width.
const contentStyle = computed(() => `padding: 28px 32px; max-width: ${['logs', 'events', 'history'].includes(active.value) ? 1280 : active.value === 'games' ? 920 : 760}px;`)
const collapsed = ref(false)

const osTheme = useOsTheme()
const theme = computed(() => {
  const mode = ui.value.theme === 'auto' ? osTheme.value : ui.value.theme
  return mode === 'dark' ? darkTheme : null
})
const overrides = computed<GlobalThemeOverrides>(() => {
  const size = `${ui.value.fontSize}px`
  const base: GlobalThemeOverrides = {
    common: {
      ...accentPalette(ui.value.accent),
      fontSize: size,
      fontSizeMedium: size,
      fontFamily: '"Microsoft YaHei UI", "PingFang SC", system-ui, sans-serif',
      borderRadius: '6px'
    }
  }
  const preset = consoleStyleOverrides(ui.value.consoleStyle, theme.value !== null)
  return { ...base, ...preset, common: { ...base.common, ...preset.common } }
})

// User CSS goes in its own <style> at the end of <head>, so it overrides Naive UI's injected styles on ties.
const customStyle = document.createElement('style')
customStyle.id = 'console-css'
document.head.appendChild(customStyle)
watchEffect(() => {
  customStyle.textContent = ui.value.consoleCss
})

onMounted(async () => {
  // Opened for a page (the pet's game invitation), or asked to switch while open.
  const go = (page: string): void => {
    if (page in pages) activePage.value = page
    if (page === 'games') void loadGames()
  }
  window.petConsole.onNavigate(go)
  void window.petConsole.initialPage().then((p) => p && go(p))
  ui.value = await window.petConsole.getUi()
})
</script>

<template>
  <n-config-provider :theme="theme" :theme-overrides="overrides" style="height: 100%">
    <n-global-style />
    <n-message-provider>
      <n-layout has-sider style="height: 100%">
        <n-layout-sider
          bordered
          collapse-mode="width"
          :collapsed-width="64"
          :width="200"
          :collapsed="collapsed"
          show-trigger
          @collapse="collapsed = true"
          @expand="collapsed = false"
        >
          <div class="brand" :class="{ collapsed }">
            <span class="dot" :style="{ background: ui.accent }" />
            <span v-if="!collapsed">桌宠控制台</span>
          </div>
          <backend-status :collapsed="collapsed" />
          <div class="all-off" :class="{ collapsed }">
            <all-off-button :collapsed="collapsed" @done="reloadCurrent" />
          </div>
          <div class="nav" title="按住拖动可以在同一组里调整顺序" @pointerdown="navDrag.start">
            <n-menu
            :value="active"
            :options="menu"
            :collapsed="collapsed"
            :collapsed-width="64"
            :collapsed-icon-size="20"
            @update:value="(k: string) => (active = k as PageKey)"
            />
          </div>
        </n-layout-sider>
        <n-layout-content :content-style="contentStyle" :native-scrollbar="false">
          <component :is="current.component" v-bind="current.props" :key="active" />
        </n-layout-content>
      </n-layout>
    </n-message-provider>
  </n-config-provider>
</template>

<style>
/* Long-press drag sorting (drag-sort.ts): the menu here and the block list on the 提示词 page. */
body.drag-sorting {
  user-select: none;
  cursor: grabbing;
}
body.drag-sorting * {
  cursor: grabbing !important;
}
.drag-item {
  opacity: 0.55;
  outline: 1px dashed currentColor;
  outline-offset: -1px;
  border-radius: 6px;
}
</style>

<style scoped>
.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 56px;
  padding: 0 24px;
  font-weight: 600;
  font-size: 1.05em;
  white-space: nowrap;
  overflow: hidden;
}
.all-off {
  padding: 0 16px 12px;
}
.all-off.collapsed {
  padding: 0 8px 12px;
}
.brand.collapsed {
  padding: 0;
  justify-content: center;
}
.dot {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  flex: none;
}
</style>
