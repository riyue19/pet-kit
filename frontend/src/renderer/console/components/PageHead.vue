<script setup lang="ts">
/** A page's title row. Extra things for the row (tags, switches, buttons) go in the slot. */
withDefaults(defineProps<{ title: string; gap?: number }>(), { gap: 20 })
</script>

<template>
  <div class="page-head" :style="{ marginBottom: `${gap}px` }">
    <h2 class="page-title">{{ title }}</h2>
    <slot />
  </div>
</template>

<style scoped>
.page-head {
  display: flex;
  align-items: center;
  gap: 12px;
}
.page-title {
  margin: 0;
  font-size: 1.4em;
  font-weight: 600;
}
</style>
