<script setup lang="ts">
// 平台简称色块徽标（无外部 logo 依赖，纯文字 + 品牌近似色）。
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
    {{ meta.label }}
  </span>
</template>

<style scoped>
.platform-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 44px;
  height: 28px;
  padding: 0 8px;
  border-radius: 6px;
  color: #fff;
  font-size: 12px;
  font-weight: 600;
  line-height: 1;
  white-space: nowrap;
}
.platform-badge.is-small {
  min-width: 32px;
  height: 22px;
  font-size: 11px;
  border-radius: 5px;
}
</style>
