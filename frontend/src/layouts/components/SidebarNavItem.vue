<script setup lang="ts">
// M11-A 侧栏菜单项：横向布局（图标左 + 中文标签右）+ active 紫底白字 pill
// - 视觉与 M10-15 TopNavIcon 一致（紫 #7c4dff / 8% hover / active 紫底白字）
// - 区别：TopNavIcon 是垂直方块（68px 竖栏专用）；本组件是横排行项（220px 侧栏专用）
import { computed } from 'vue'
import type { Component } from 'vue'
import { useRoute, useRouter } from 'vue-router'

interface Props {
  path: string
  label: string
  icon: Component
  exact?: boolean
}

const props = withDefaults(defineProps<Props>(), { exact: false })

const route = useRoute()
const router = useRouter()

const isActive = computed<boolean>(() => {
  if (props.exact) return route.path === props.path
  return route.path === props.path || route.path.startsWith(props.path + '/')
})

function go(): void {
  if (!isActive.value) {
    void router.push(props.path)
  }
}
</script>

<template>
  <button
    type="button"
    :class="['sidebar-nav-item', { 'is-active': isActive }]"
    :aria-current="isActive ? 'page' : undefined"
    @click="go"
  >
    <component :is="icon" class="nav-icon" />
    <span class="nav-label">{{ label }}</span>
  </button>
</template>

<style scoped>
.sidebar-nav-item {
  display: flex;
  align-items: center;
  gap: 12px;
  width: calc(100% - 16px);
  margin: 2px 8px;
  padding: 8px 12px;
  border: none;
  background: transparent;
  border-radius: 8px;
  color: #4a4a4a;
  font-size: 14px;
  line-height: 1.4;
  cursor: pointer;
  text-align: left;
  transition: background-color 0.15s, color 0.15s;
}

.sidebar-nav-item:hover {
  background: rgba(124, 77, 255, 0.08);
  color: #7c4dff;
}

.sidebar-nav-item.is-active {
  background: #7c4dff;
  color: #fff;
}

.sidebar-nav-item.is-active:hover {
  background: #6a3ff0;
  color: #fff;
}

.nav-icon {
  font-size: 16px;
  line-height: 1;
  flex-shrink: 0;
}

.nav-label {
  flex: 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.sidebar-nav-item:focus-visible {
  outline: 2px solid #7c4dff;
  outline-offset: -2px;
}
</style>
