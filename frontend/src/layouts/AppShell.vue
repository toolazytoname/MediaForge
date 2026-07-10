<script setup lang="ts">
// M10-15 阶段 H（修正 2 / amend）：蚁小二式 9 菜单 + 头部头像 + 主区域 hero 卡 + 4 选 1 弹窗
// - 侧栏：紫 logo「⬢ MediaForge」+ 9 个 TopNavIcon 垂直堆叠（不再保留单独设置项；改放头像菜单）
// - 顶部：64px 高 header，左上角 32×32 圆头像（UserAvatarMenu 触发下拉：用户名/设置/退出）
// - 主区域：root 路由时显示 hero 大卡；其他路由显示 router-view
// - 弹窗：hero 卡点击 / 侧栏「发布」菜单 → StartPublishModal 弹 4 选 1；图文跳 /creation，其余占位
// - 路由：`/roadmap/:feature` 通用占位已支持 /roadmap/comments 与 /roadmap/ai，无需新增
// - 保留上次视觉：68px 竖栏、紫底白字 active、8% 紫 hover、a-tooltip placement="right"、flex-col 图标按钮
import { computed, ref } from 'vue'
import type { Component } from 'vue'
import { useRoute } from 'vue-router'
import {
  SendOutlined,
  TeamOutlined,
  BarChartOutlined,
  CodeOutlined,
  MessageOutlined,
  EditOutlined,
  RobotOutlined,
  UserOutlined,
  PictureOutlined,
} from '@ant-design/icons-vue'
import TopNavIcon from './components/TopNavIcon.vue'
import UserAvatarMenu from './components/UserAvatarMenu.vue'
import StartPublishHero from './components/StartPublishHero.vue'
import StartPublishModal from './components/StartPublishModal.vue'

interface NavItem {
  path: string
  label: string
  icon: Component
  exact?: boolean
}

// 9 个侧栏菜单（完全照抄蚁小二 9 个菜单，不再"映射"到原 11 页）
// 团队（UserOutlined）共用账号页 /accounts；私信评论/小蚁占位走 /roadmap/:feature
const mainItems: ReadonlyArray<NavItem> = [
  { path: '/creation', label: '发布', icon: SendOutlined },
  { path: '/accounts', label: '账号', icon: TeamOutlined },
  { path: '/analytics', label: '数据', icon: BarChartOutlined },
  { path: '/runs', label: 'CLI', icon: CodeOutlined },
  { path: '/roadmap/comments', label: '私信评论', icon: MessageOutlined },
  { path: '/contents', label: '创作', icon: EditOutlined },
  { path: '/roadmap/ai', label: '小蚁', icon: RobotOutlined },
  { path: '/accounts', label: '团队', icon: UserOutlined },
  { path: '/topics', label: '素材', icon: PictureOutlined },
]

const route = useRoute()
const modalOpen = ref<boolean>(false)

// 仅在 root 路由显示 hero 大卡；进入其他路由由 router-view 全屏展示
const showHero = computed<boolean>(() => route.path === '/')

function openModal(): void {
  modalOpen.value = true
}
</script>

<template>
  <a-layout style="min-height: 100vh">
    <!-- 顶部 header：64px 高，左上角头像菜单 -->
    <a-layout-header class="app-header">
      <UserAvatarMenu />
    </a-layout-header>

    <a-layout>
      <!-- 68px 极窄左竖栏 -->
      <a-layout-sider
        width="68"
        class="app-sider"
      >
        <!-- 顶部 logo：紫色 ⬢ MediaForge -->
        <div class="sidebar-logo">
          <div class="logo-icon">⬢</div>
        </div>

        <!-- 中间：9 个主菜单垂直堆叠 -->
        <div class="sidebar-menu">
          <TopNavIcon
            v-for="item in mainItems"
            :key="item.path + '|' + item.label"
            :path="item.path"
            :label="item.label"
            :icon="item.icon"
            :exact="item.exact === true"
          />
        </div>
      </a-layout-sider>

      <!-- 主区域：sider 是 layout 子元素，无需手动 marginLeft -->
      <a-layout-content class="app-content">
        <div class="content-inner">
          <!-- hero 大卡：仅 root 路由显示 -->
          <StartPublishHero v-if="showHero" @click="openModal" />
          <router-view />
        </div>
      </a-layout-content>
    </a-layout>

    <!-- 4 选 1 弹窗：hero 卡点击或侧栏「发布」触发 -->
    <StartPublishModal v-model:open="modalOpen" />
  </a-layout>
</template>

<style scoped>
.app-header {
  background: #fff;
  height: 64px;
  line-height: 64px;
  padding: 0 16px;
  border-bottom: 1px solid #f0f0f0;
  display: flex;
  align-items: center;
  position: sticky;
  top: 0;
  z-index: 10;
}

.app-sider {
  background: #fff;
  border-right: 1px solid #f0f0f0;
  position: sticky;
  top: 64px; /* 顶 header 占 64px */
  height: calc(100vh - 64px);
  overflow: auto;
  display: flex;
  flex-direction: column;
  padding: 0;
}

/* logo：竖栏顶部 */
.sidebar-logo {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 60px;
  border-bottom: 1px solid #f0f0f0;
  flex-shrink: 0;
}

.logo-icon {
  font-size: 28px;
  color: #7c4dff;
  line-height: 1;
}

/* 中间菜单：垂直堆叠（父级 flex-col，子项 TopNavIcon 自身也是 flex-col） */
.sidebar-menu {
  display: flex;
  flex-direction: column;
  align-items: center;
  flex: 1;
  padding-top: 8px;
  min-height: 0;
}

/* 主区域 overflow 多重兜底（不动业务页面） */
.app-content {
  margin: 0;
  padding: 24px;
  background: #f8f8fa;
  min-height: calc(100vh - 64px);
  overflow-x: auto;
  max-width: 100%;
  box-sizing: border-box;
}

.content-inner {
  min-width: 0;
  max-width: 100%;
  overflow: hidden;
}

/* 卡片标题 / 卡片体兜底：长串内容自动换行（不撑爆外层） */
:deep(.ant-card-head-title) {
  word-break: break-word;
  white-space: normal;
}

:deep(.ant-card-body) {
  word-break: break-word;
}

/* 窄屏响应式：内容区收紧 padding；竖栏不变（68px 永远显示） */
@media (max-width: 1024px) {
  .app-content {
    padding: 16px;
  }
}

@media (max-width: 640px) {
  .app-content {
    padding: 12px;
  }
}
</style>