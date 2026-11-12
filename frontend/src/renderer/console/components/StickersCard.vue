<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { NButton, NCard, NEmpty, NInput, NPopconfirm, NSpace, NSpin, NSwitch, useMessage } from 'naive-ui'
import { MAX_STICKERS, STICKER_NAME_MAX, stickerNameError } from '../../../shared/stickers'
import type { StickerEntry } from '../../../shared/stickers'
import { errorText } from '../store'

const message = useMessage()
const loading = ref(true)
const importing = ref(false)
const stickers = ref<StickerEntry[]>([])
// Local edit values and error flags, keyed by sticker id.
const drafts = reactive<Record<string, string>>({})
const invalid = reactive<Record<string, boolean>>({})

const full = computed(() => stickers.value.length >= MAX_STICKERS)

function setList(list: StickerEntry[]): void {
  stickers.value = list
  for (const k of Object.keys(drafts)) delete drafts[k]
  for (const k of Object.keys(invalid)) delete invalid[k]
  for (const s of list) drafts[s.id] = s.name
}

onMounted(async () => {
  try {
    setList(await window.petConsole.stickers())
  } catch (e) {
    message.error(errorText(e), { duration: 6000 })
  } finally {
    loading.value = false
  }
})

async function importPictures(): Promise<void> {
  importing.value = true
  try {
    const r = await window.petConsole.importStickers()
    setList(r.stickers)
    for (const e of r.errors) message.warning(e, { duration: 6000 })
  } catch (e) {
    message.error(errorText(e), { duration: 6000 })
  } finally {
    importing.value = false
  }
}

async function commitName(s: StickerEntry): Promise<void> {
  const value = (drafts[s.id] ?? '').trim()
  if (value === s.name) {
    drafts[s.id] = s.name
    invalid[s.id] = false
    return
  }
  const others = stickers.value.filter((o) => o.id !== s.id).map((o) => o.name)
  const err = stickerNameError(value, others)
  if (err !== null) {
    // Keep what was typed (marked red) so it can be corrected instead of retyped.
    invalid[s.id] = true
    message.error(err)
    return
  }
  invalid[s.id] = false
  try {
    setList(await window.petConsole.updateSticker(s.id, { name: value }))
  } catch (e) {
    message.error(errorText(e), { duration: 6000 })
    drafts[s.id] = s.name
  }
}

async function toggle(s: StickerEntry, enabled: boolean): Promise<void> {
  try {
    setList(await window.petConsole.updateSticker(s.id, { enabled }))
  } catch (e) {
    message.error(errorText(e), { duration: 6000 })
  }
}

async function remove(s: StickerEntry): Promise<void> {
  try {
    setList(await window.petConsole.deleteSticker(s.id))
  } catch (e) {
    message.error(errorText(e), { duration: 6000 })
  }
}
</script>

<template>
  <n-card>
    <n-spin :show="loading">
      <n-space vertical :size="12">
        <span class="hint">导入图片并给每张起一个备注名。对话时会把启用的备注名发给模型，由它决定要不要发、发哪一张；图片本身不会发给模型。备注名写清楚这张图表达什么，例如它的情绪或用途。这里的修改立即生效。</span>
        <n-space align="center">
          <n-button :loading="importing" :disabled="full" @click="importPictures">导入图片</n-button>
          <span>{{ stickers.length }} / {{ MAX_STICKERS }}</span>
        </n-space>
        <n-empty v-if="!stickers.length && !loading" description="还没有表情包" />
        <div v-for="s in stickers" :key="s.id" class="item">
          <div class="thumb">
            <img v-if="s.thumb" :src="s.thumb" alt="" />
            <span v-else class="lost">图片丢失</span>
          </div>
          <n-input
            v-model:value="drafts[s.id]"
            class="name"
            :maxlength="STICKER_NAME_MAX"
            :status="invalid[s.id] ? 'error' : undefined"
            placeholder="备注名"
            @blur="commitName(s)"
            @keyup.enter="commitName(s)"
          />
          <n-switch :value="s.enabled" title="关掉后这张不会发给模型选用" @update:value="(v: boolean) => toggle(s, v)" />
          <span class="hint">启用</span>
          <n-popconfirm @positive-click="remove(s)">
            <template #trigger>
              <n-button size="small">删除</n-button>
            </template>
            删除这张表情包？图片文件也会一起删掉。
          </n-popconfirm>
        </div>
      </n-space>
    </n-spin>
  </n-card>
</template>

<style scoped>
.item {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
}
.thumb {
  width: 72px;
  height: 72px;
  flex: none;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px solid rgba(128, 128, 128, 0.35);
  border-radius: 6px;
  overflow: hidden;
}
.thumb img {
  width: 100%;
  height: 100%;
  object-fit: contain;
}
.lost {
  font-size: 0.8em;
  opacity: 0.6;
}
.name {
  flex: 1;
  max-width: 320px;
}
</style>
