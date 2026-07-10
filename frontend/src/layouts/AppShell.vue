<script setup lang="ts">
// M11-A 侧栏 IA 拨正：分模块对标蚁小二 + 保留 MediaForge 内容生产一等公民
// - 侧栏分 6 组：概览 / 发布 / 账号 / 数据 / 内容生产 / 运营；规划中模块单独组（占位，可导航不假装可用）
// - 视觉：220px 宽 sidebar、紫 #7c4dff active pill、横排图标+标签（用 SidebarNavItem）
// - 路由：本任务加 `/publish` 重定向到 `/publish/records`（M11-B 会替换为新发布中心组件）
// - 占位：`/roadmap/:feature` 仍走 /views/EmptyStub.vue；规划中组的导航项也在该路由上
import { computed, ref } from 'vue'
import type { Component } from 'vue'
import { useRoute } from 'vue-router'
import {
  HomeOutlined,
  SendOutlined,
  TeamOutlined,
  BarChartOutlined,
  EditOutlined,
  BulbOutlined,
  DatabaseOutlined,
  AuditOutlined,
  CodeOutlined,
  SettingOutlined,
  MessageOutlined,
  RobotOutlined,
} from '@ant-design/icons-vue'
import SidebarNavItem from './components/SidebarNavItem.vue'
import UserAvatarMenu from './components/UserAvatarMenu.vue'
import StartPublishHero from './components/StartPublishHero.vue'
import StartPublishModal from './components/StartPublishModal.vue'

interface NavItem {
  path: string
  label: string
  icon: Component
  exact?: boolean
}

interface NavGroup {
  label: string
  items: ReadonlyArray<NavItem>
}

// 6 真实分组 + 1 规划中占位组
// 内容生产保留为真实一等公民（创作/选题/内容/审核），不学蚁小二删掉
const groups: ReadonlyArray<NavGroup> = [
  {
    label: '概览',
    items: [
      { path: '/', label: '仪表盘', icon: HomeOutlined, exact: true },
    ],
  },
  {
    label: '发布',
    // M11-B 会把 `/publish` 替换为正式发布中心组件；本任务先用重定向保持可点
    items: [{ path: '/publish', label: '发布中心', icon: SendOutlined }],
  },
  {
    label: '账号',
    items: [{ path: '/accounts', label: '账号', icon: TeamOutlined }],
  },
  {
    label: '数据',
    items: [{ path: '/analytics', label: '数据', icon: BarChartOutlined }],
  },
  {
    label: '内容生产',
    items: [
      { path: '/creation', label: '创作', icon: EditOutlined },
      { path: '/topics', label: '选题池', icon: BulbOutlined },
      { path: '/contents', label: '内容库', icon: DatabaseOutlined },
      { path: '/review', label: '审核台', icon: AuditOutlined },
    ],
  },
  {
    label: '运营',
    items: [
      { path: '/runs', label: '运行台', icon: CodeOutlined },
      { path: '/settings', label: '设置', icon: SettingOutlined },
    ],
  },
  {
    label: '规划中',
    items: [
      { path: '/roadmap/comments', label: '私信评论', icon: MessageOutlined },
      { path: '/roadmap/ai', label: '小蚁', icon: RobotOutlined },
    ],
  },
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
      <!-- 220px 侧栏：分组导航 + 紫底 active pill -->
      <a-layout-sider
        width="220"
        class="app-sider"
      >
        <!-- 顶部 logo：紫色 ⬢ MediaForge -->
        <div class="sidebar-logo">
          <div class="logo-icon">⬢</div>
          <div class="logo-text">MediaForge</div>
        </div>

        <!-- 中间：分组导航 -->
        <nav class="sidebar-nav" aria-label="主导航">
          <div v-for="group in groups" :key="group.label" class="nav-group">
            <div class="nav-group-label">{{ group.label }}</div>
            <SidebarNavItem
              v-for="item in group.items"
              :key="item.path + '|' + item.label"
              :path="item.path"
              :label="item.label"
              :icon="item.icon"
              :exact="item.exact === true"
            />
          </div>
        </nav>
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

/* logo：竖栏顶部，左侧 logo 图标 + 右侧品牌名 */
.sidebar-logo {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 16px;
  height: 56px;
  border-bottom: 1px solid #f0f0f0;
  flex-shrink: 0;
}

.logo-icon {
  font-size: 26px;
  color: #7c4dff;
  line-height: 1;
}

.logo-text {
  font-size: 15px;
  font-weight: 600;
  color: #1f1f1f;
  letter-spacing: 0.2px;
}

.sidebar-nav {
  display: flex;
  flex-direction: column;
  flex: 1;
  padding: 8px 0 16px;
  min-height: 0;
  overflow-y: auto;
}

.nav-group + .nav-group {
  margin-top: 12px;
}

.nav-group-label {
  font-size: 11px;
  font-weight: 600;
  color: #8c8c8c;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  padding: 4px 20px 6px;
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

/* 窄屏响应式：内容区收紧 padding；侧栏收缩到 68px 图标列（只露图标，组标题隐藏） */
@media (max-width: 1024px) {
  .app-sider {
    width: 68px !important;
    min-width: 68px !important;
  }
  .logo-text {
    display: none;
  }
  .nav-group-label {
    display: none;
  }
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
