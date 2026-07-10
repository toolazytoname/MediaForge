<script setup lang="ts">
// M10 P2 阶段 F: 精修左侧栏——蚁小二式浅色侧栏 + 紫色主色 + outline 图标 + active pill 高亮
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import {
  DashboardOutlined,
  FileTextOutlined,
  CheckCircleOutlined,
  CalendarOutlined,
  BarChartOutlined,
  SettingOutlined,
  PlayCircleOutlined,
  TeamOutlined,
  BulbOutlined,
  EditOutlined,
} from '@ant-design/icons-vue'

const route = useRoute()

interface MenuItem {
  path: string
  label: string
  icon: any
}

interface MenuGroup {
  title: string
  items: MenuItem[]
}

const groups: MenuGroup[] = [
  {
    title: '概览',
    items: [{ path: '/', label: 'Dashboard', icon: DashboardOutlined }],
  },
  {
    title: '内容生产',
    items: [
      { path: '/topics', label: '选题池', icon: BulbOutlined },
      { path: '/creation', label: '图文创作', icon: EditOutlined },
      { path: '/contents', label: '内容库', icon: FileTextOutlined },
      { path: '/review', label: '审核台', icon: CheckCircleOutlined },
    ],
  },
  {
    title: '分发',
    items: [
      { path: '/publish/calendar', label: '发布日历', icon: CalendarOutlined },
      { path: '/publish/records', label: '发布记录', icon: BarChartOutlined },
      { path: '/accounts', label: '账号管理', icon: TeamOutlined },
    ],
  },
  {
    title: '数据',
    items: [{ path: '/analytics', label: '数据看板', icon: BarChartOutlined }],
  },
  {
    title: '运营',
    items: [
      { path: '/runs', label: '运行台', icon: PlayCircleOutlined },
      { path: '/settings', label: '设置', icon: SettingOutlined },
    ],
  },
]

const currentPath = computed(() => route.path)

// 头部当前页标题：path → label 反查（菜单项少，直接用 Map）
const pathToTitle = computed<Map<string, string>>(() => {
  const m = new Map<string, string>()
  for (const g of groups) {
    for (const it of g.items) {
      m.set(it.path, it.label)
    }
  }
  return m
})

const currentPageTitle = computed(() => pathToTitle.value.get(currentPath.value) ?? 'MediaForge')
</script>

<template>
  <a-layout style="min-height: 100vh">
    <a-layout-sider
      width="220"
      theme="light"
      :style="{ background: '#fff', borderRight: '1px solid #f0f0f0' }"
    >
      <div class="logo">
        <span style="font-size: 22px">⬢ MediaForge</span>
        <div class="logo-subtitle">自媒体矩阵流水线</div>
      </div>
      <a-menu mode="inline" :selected-keys="[currentPath]" style="border-right: 0">
        <template v-for="g in groups" :key="g.title">
          <a-menu-item-group :title="g.title">
            <a-menu-item v-for="it in g.items" :key="it.path">
              <router-link :to="it.path" class="menu-link">
                <component :is="it.icon" />
                {{ it.label }}
              </router-link>
            </a-menu-item>
          </a-menu-item-group>
        </template>
      </a-menu>
    </a-layout-sider>
    <a-layout>
      <a-layout-header class="app-header">
        <span class="header-title">{{ currentPageTitle }}</span>
        <a-tag color="purple">v0.3.0</a-tag>
      </a-layout-header>
      <a-layout-content class="app-content">
        <router-view />
      </a-layout-content>
    </a-layout>
  </a-layout>
</template>

<style scoped>
.logo {
  color: #7c4dff;
  font-size: 18px;
  font-weight: bold;
  padding: 20px 16px;
  text-align: center;
  border-bottom: 1px solid #f0f0f0;
}
.logo-subtitle {
  font-size: 11px;
  color: #999;
  font-weight: normal;
  margin-top: 4px;
}
.app-header {
  background: #fff;
  padding: 0 24px;
  border-bottom: 1px solid #f0f0f0;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.header-title {
  font-size: 16px;
  font-weight: 500;
  color: #1a1a1a;
}
.app-content {
  margin: 16px;
  padding: 24px;
  background: #f8f8fa;
  min-height: calc(100vh - 64px);
}
.menu-link {
  color: rgba(0, 0, 0, 0.85);
  text-decoration: none;
  display: flex;
  align-items: center;
  gap: 10px;
}
.menu-link:hover {
  color: #7c4dff;
}
:deep(.ant-menu-item-selected .menu-link) {
  color: #7c4dff;
  font-weight: 500;
}
:deep(.ant-menu-item-group-title) {
  color: #999;
  font-size: 11px;
  letter-spacing: 0.5px;
  text-transform: uppercase;
  padding-left: 16px;
}
</style>