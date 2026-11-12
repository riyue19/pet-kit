<script setup lang="ts">
import { onMounted } from 'vue'
import { NButton } from 'naive-ui'
import BackendOffline from '../components/BackendOffline.vue'
import { fishing, fishingView, loadFishing } from '../fishing'
import FishingGame from './FishingGame.vue'
import FishingScene from './FishingScene.vue'

defineEmits<{ back: [] }>()

onMounted(() => {
  if (!fishing.loaded) void loadFishing()
})
</script>

<template>
  <backend-offline v-if="fishing.offline || !fishing.loaded" :error="fishing.error" :loading="fishing.loading" @retry="loadFishing" />
  <!-- The scene: only the animation and the cast / reel / bait buttons. Everything else is one click away. -->
  <fishing-scene v-else-if="fishingView === 'scene'" @manage="fishingView = 'manage'" />
  <template v-else>
    <n-button size="small" class="to-scene" @click="fishingView = 'scene'">← 回到钓鱼画面</n-button>
    <fishing-game />
  </template>
</template>

<style scoped>
.to-scene {
  margin-bottom: 12px;
}
</style>
