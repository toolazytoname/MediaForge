<script setup lang="ts">
// M10-7 AppShell：蚁小二式左侧栏 App Shell
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
} from '@ant-design/icons-vue'

const route = useRoute()

interface MenuGroup {
  title: string
  items: { path: string; label: string; icon: any }[]
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
function isActive(path: string): boolean {
  if (path === '/') return currentPath.value === '/'
  return currentPath.value === path || currentPath.value.startsWith(path + '/')
}
</script>

<template>
  <a-layout style="min-height: 100vh">
    <a-layout-sider width="220" theme="dark">
      <div class="logo">MediaForge</div>
      <a-menu theme="dark" mode="inline" :selected-keys="[currentPath]">
        <template v-for="g in groups" :key="g.title">
          <a-menu-item-group :title="g.title">
            <a-menu-item v-for="it in g.items" :key="it.path">
              <router-link :to="it.path">
                <component :is="it.icon" />
                {{ it.label }}
              </router-link>
            </a-menu-item>
          </a-menu-item-group>
        </template>
      </a-menu>
    </a-layout-sider>
    <a-layout>
      <a-layout-header style="background: #fff; padding: 0 16px">
        <span style="font-size: 18px">蚁小二形态 · 控制台</span>
      </a-layout-header>
      <a-layout-content style="margin: 16px; padding: 16px; background: #fff">
        <router-view />
      </a-layout-content>
    </a-layout>
  </a-layout>
</template>

<style scoped>
.logo {
  color: #fff;
  font-size: 18px;
  font-weight: bold;
  padding: 16px;
  text-align: center;
}
</style>
