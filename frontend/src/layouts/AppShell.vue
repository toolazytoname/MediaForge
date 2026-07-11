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
  <div class="shell">
    <!-- 全高左栏：logo 顶 / 分组导航 中(滚动) / 用户 底 -->
    <aside class="app-sider">
      <!-- 顶部 logo：紫色 ⬢ MediaForge -->
      <div class="sidebar-logo">
        <div class="logo-icon">⬢</div>
        <div class="logo-text">MediaForge</div>
      </div>

      <!-- 中间：分组导航（唯一滚动区） -->
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

      <!-- 底部：用户头像菜单（贴底，替代原全宽空顶栏） -->
      <div class="sidebar-footer">
        <UserAvatarMenu />
        <span class="sidebar-footer-name">lazy</span>
      </div>
    </aside>

    <!-- 主区域：右侧独立滚动，无全局空顶带 -->
    <main class="app-content">
      <div class="content-inner">
        <!-- hero 大卡：仅 root 路由显示 -->
        <StartPublishHero v-if="showHero" @click="openModal" />
        <router-view />
      </div>
    </main>

    <!-- 4 选 1 弹窗：hero 卡点击或侧栏「发布」触发 -->
    <StartPublishModal v-model:open="modalOpen" />
  </div>
</template>

<style scoped>
/* 全高两栏：左栏 fixed，右侧内容让出 220px，各自独立滚动 */
.shell {
  min-height: 100vh;
}

.app-sider {
  position: fixed;
  left: 0;
  top: 0;
  width: 220px;
  height: 100vh;
  background: #fff;
  border-right: 1px solid #f0f0f0;
  display: flex;
  flex-direction: column;
  z-index: 20;
}

/* logo：竖栏顶部，左侧 logo 图标 + 右侧品牌名 */
.sidebar-logo {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 20px;
  height: 60px;
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

/* 导航是唯一滚动区 */
.sidebar-nav {
  display: flex;
  flex-direction: column;
  flex: 1;
  padding: 12px 0;
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

/* 底部用户区：贴底，替代原来那条全宽空顶栏 */
.sidebar-footer {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 20px;
  border-top: 1px solid #f0f0f0;
}

.sidebar-footer-name {
  font-size: 13px;
  font-weight: 500;
  color: #595959;
}

/* 主区域：让出 220px 左栏，独立滚动 */
.app-content {
  margin-left: 220px;
  min-height: 100vh;
  padding: 24px;
  background: #f8f8fa;
  box-sizing: border-box;
}

.content-inner {
  max-width: 1280px;
  margin: 0 auto;
  min-width: 0;
}

/* 卡片标题 / 卡片体兜底：长串内容自动换行（不撑爆外层） */
:deep(.ant-card-head-title) {
  word-break: break-word;
  white-space: normal;
}

:deep(.ant-card-body) {
  word-break: break-word;
}

/* 窄屏响应式：左栏收到 64px 只露图标，内容区让出 64px */
@media (max-width: 1024px) {
  .app-sider {
    width: 64px;
  }
  .logo-text,
  .nav-group-label,
  .sidebar-footer-name {
    display: none;
  }
  .sidebar-footer {
    justify-content: center;
    padding: 12px 0;
  }
  .app-content {
    margin-left: 64px;
    padding: 16px;
  }
}

@media (max-width: 640px) {
  .app-content {
    padding: 12px;
  }
}
</style>
