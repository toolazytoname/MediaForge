<script setup lang="ts">
// M10-15 阶段 H（修正）：竖向图标按钮（蚁小二原版）
// - 内部 flex-col：图标在上 + 中文 label 在下
// - a-tooltip 右侧浮显示完整中文名（冗余提示，hover 也可见 label）
// - active 紫底白字，hover 紫 8% 透明背景
import { computed } from 'vue'
import type { Component } from 'vue'
import { useRoute, useRouter } from 'vue-router'

interface Props {
  // 路由路径，点击跳转
  path: string
  // 完整中文名（hover 弹 a-tooltip 右侧浮 + 按钮内 label 显示）
  label: string
  // AntD outline 图标组件
  icon: Component
  // 仅精确匹配（如 `/` 不希望匹配所有子路径）
  exact?: boolean
}

const props = withDefaults(defineProps<Props>(), { exact: false })

const route = useRoute()
const router = useRouter()

// 高亮判断：精确模式只匹配路径完全相等，否则前缀匹配
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
  <a-tooltip :title="label" placement="right">
    <a-button
      type="text"
      :class="['top-nav-icon', { 'is-active': isActive }]"
      @click="go"
    >
      <component :is="icon" class="nav-icon" />
      <span class="nav-label">{{ label }}</span>
    </a-button>
  </a-tooltip>
</template>

<style scoped>
.top-nav-icon {
  display: flex !important;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 54px;
  width: 54px;
  margin: 4px auto;
  padding: 0;
  border-radius: 8px;
  transition: background-color 0.2s, color 0.2s;
}

.top-nav-icon:hover {
  background: rgba(124, 77, 255, 0.08); /* 紫色 8% 透明 */
  color: #7c4dff;
}

.top-nav-icon.is-active {
  background: #7c4dff;
  color: #fff;
}

.top-nav-icon.is-active:hover {
  background: #6a3ff0;
  color: #fff;
}

.nav-icon {
  font-size: 20px;
  line-height: 1;
  margin-bottom: 2px;
}

.nav-label {
  font-size: 10px;
  line-height: 1;
  color: inherit;
}
</style>