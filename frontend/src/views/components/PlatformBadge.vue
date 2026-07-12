<script setup lang="ts">
// 平台徽标：有品牌 logo（simple-icons）画圆形色底 + 白色图标；没有(头条/视频号)回退文字色块。
import { computed } from 'vue'
import { platformMeta } from './platformMeta'

interface Props {
  platform: string
  size?: 'small' | 'default'
}

const props = withDefaults(defineProps<Props>(), { size: 'default' })

const meta = computed(() => platformMeta(props.platform))
</script>

<template>
  <span
    class="platform-badge"
    :class="size === 'small' ? 'is-small' : ''"
    :style="{ background: meta.color }"
    :title="meta.label"
  >
    <svg v-if="meta.iconPath" viewBox="0 0 24 24" class="platform-icon" fill="#fff">
      <path :d="meta.iconPath" />
    </svg>
    <span v-else class="platform-text">{{ meta.label }}</span>
  </span>
</template>

<style scoped>
.platform-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 28px;
  width: 28px;
  height: 28px;
  border-radius: 50%;
  color: #fff;
  font-size: 12px;
  font-weight: 600;
  line-height: 1;
  white-space: nowrap;
  flex-shrink: 0;
}
.platform-badge.is-small {
  min-width: 22px;
  width: 22px;
  height: 22px;
}
.platform-badge:has(.platform-text) {
  min-width: 44px;
  width: auto;
  border-radius: 6px;
  padding: 0 8px;
}
.platform-badge.is-small:has(.platform-text) {
  min-width: 32px;
  padding: 0 6px;
  border-radius: 5px;
}
.platform-icon {
  width: 60%;
  height: 60%;
}
.platform-text {
  font-size: inherit;
}
</style>
