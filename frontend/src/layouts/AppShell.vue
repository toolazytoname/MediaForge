<script setup lang="ts">
// R3：普通创作导航与内部运行工具分层，旧路由继续保留。
import { computed, ref } from 'vue'
import type { Component } from 'vue'
import { useRoute } from 'vue-router'
import {
  BarChartOutlined,
  BulbOutlined,
  CodeOutlined,
  DatabaseOutlined,
  FileTextOutlined,
  FolderOpenOutlined,
  HomeOutlined,
  SendOutlined,
  SettingOutlined,
  AuditOutlined,
} from '@ant-design/icons-vue'
import SidebarNavItem from './components/SidebarNavItem.vue'
import UserAvatarMenu from './components/UserAvatarMenu.vue'

interface NavItem { path: string; label: string; icon: Component; exact?: boolean }

const primaryItems: ReadonlyArray<NavItem> = [
  { path: '/', label: '开始创作', icon: HomeOutlined, exact: true },
  { path: '/ideas', label: '灵感', icon: BulbOutlined },
  { path: '/projects', label: '项目', icon: FolderOpenOutlined },
  { path: '/roadmap/library', label: '资产', icon: FileTextOutlined },
  { path: '/publish', label: '发布', icon: SendOutlined },
  { path: '/analytics', label: '复盘', icon: BarChartOutlined },
]

const developerItems: ReadonlyArray<NavItem> = [
  { path: '/creation', label: '旧创作向导', icon: FileTextOutlined },
  { path: '/creation/video', label: '视频向导', icon: FileTextOutlined },
  { path: '/topics', label: '选题状态', icon: BulbOutlined },
  { path: '/contents', label: '内容记录', icon: DatabaseOutlined },
  { path: '/review', label: '审核状态', icon: AuditOutlined },
  { path: '/runs', label: '运行状态', icon: CodeOutlined },
  { path: '/accounts', label: '账号状态', icon: FileTextOutlined },
  { path: '/settings', label: '设置', icon: SettingOutlined },
]

const developerOpen = ref(false)
const route = useRoute()
// UX-00: the creator's first screen is the article input itself.  The legacy
// workbench remains reachable after creation, but must not compete with it.
const showLegacyShell = computed(() => route.path !== '/')
</script>

<template>
  <div class="shell">
    <aside v-if="showLegacyShell" class="app-sider">
      <div class="sidebar-logo"><div class="logo-mark">M</div><div class="logo-text">MediaForge</div></div>
      <nav class="sidebar-nav" aria-label="主导航">
        <p class="nav-caption">创作工作台</p>
        <SidebarNavItem v-for="item in primaryItems" :key="item.path" v-bind="item" :exact="item.exact === true" />
      </nav>
      <div class="sidebar-footer">
        <a-button type="text" class="developer-trigger" @click="developerOpen = true"><CodeOutlined /> 开发者 / 运行状态</a-button>
        <div class="profile"><UserAvatarMenu /><span>lazy</span></div>
      </div>
    </aside>
    <main class="app-content" :class="{ 'article-first': !showLegacyShell }"><div class="content-inner"><router-view /></div></main>
    <a-drawer v-model:open="developerOpen" title="开发者 / 运行状态" placement="left" width="280">
      <p class="drawer-note">这里保留旧流水线工具和运行状态。它们不会占用日常创作入口。</p>
      <nav aria-label="开发者工具">
        <SidebarNavItem v-for="item in developerItems" :key="item.path" v-bind="item" @click="developerOpen = false" />
      </nav>
    </a-drawer>
  </div>
</template>

<style scoped>
.shell { min-height: 100vh; }
.app-sider { position: fixed; inset: 0 auto 0 0; z-index: 20; display: flex; width: 224px; height: 100vh; flex-direction: column; border-right: 1px solid #e7e0d7; background: #fbfaf7; }
.sidebar-logo { display: flex; height: 66px; align-items: center; gap: 10px; padding: 0 20px; border-bottom: 1px solid #e7e0d7; }.logo-mark { display: grid; width: 28px; height: 28px; place-items: center; border-radius: 50%; background: #2d2926; color: #fffdf8; font-family: Georgia, serif; font-weight: 700; }.logo-text { color: #292522; font-family: Georgia, 'Songti SC', serif; font-size: 17px; font-weight: 700; }
.sidebar-nav { flex: 1; overflow-y: auto; padding: 18px 0; }.nav-caption { margin: 0; padding: 0 20px 8px; color: #968b7e; font-size: 11px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }
.sidebar-footer { padding: 12px; border-top: 1px solid #e7e0d7; }.developer-trigger { width: 100%; justify-content: flex-start; color: #6f6962; font-size: 12px; }.profile { display: flex; align-items: center; gap: 9px; padding: 8px; color: #59534d; font-size: 13px; }
.app-content { min-height: 100vh; margin-left: 224px; padding: 0 32px; background: #f6f4ef; box-sizing: border-box; }.app-content.article-first { margin-left: 0; padding: 0; }.content-inner { min-width: 0; max-width: 1280px; margin: 0 auto; }.app-content.article-first .content-inner { max-width: none; }.drawer-note { margin: 0 0 14px; color: #706b65; font-size: 13px; line-height: 1.65; }
:deep(.ant-card-head-title), :deep(.ant-card-body) { word-break: break-word; white-space: normal; }
@media (max-width: 1024px) { .app-sider { width: 64px; }.logo-text, .nav-caption, .developer-trigger :deep(span:not(.anticon)), .profile span { display: none; }.sidebar-logo { justify-content: center; padding: 0; }.developer-trigger, .profile { justify-content: center; padding-inline: 0; }.app-content { margin-left: 64px; padding: 0 16px; } }
@media (max-width: 640px) { .app-content { padding: 0 12px; } }
</style>
