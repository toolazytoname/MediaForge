// M11-A 路由：保留 M10-7 全部 11 真实页 + 占位路由；M11-A 新增 `/publish` 重定向
// （M11-B 会把 `/publish` 替换为正式 PublishCenter 组件）
import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

const routes: RouteRecordRaw[] = [
  { path: '/', name: 'dashboard', component: () => import('../views/Dashboard.vue') },
  { path: '/topics', name: 'topics', component: () => import('../views/Topics.vue') },
  { path: '/contents', name: 'contents', component: () => import('../views/Contents.vue') },
  { path: '/contents/:id', name: 'content-detail', component: () => import('../views/ContentDetail.vue') },
  { path: '/review', name: 'review', component: () => import('../views/Review.vue') },
  { path: '/creation', name: 'creation', component: () => import('../views/Creation.vue') },
  { path: '/publish', name: 'publish', redirect: '/publish/records' },
  { path: '/publish/calendar', name: 'publish-calendar', component: () => import('../views/PublishCalendar.vue') },
  { path: '/publish/records', name: 'publish-records', component: () => import('../views/PublishRecords.vue') },
  { path: '/analytics', name: 'analytics', component: () => import('../views/Analytics.vue') },
  { path: '/accounts', name: 'accounts', component: () => import('../views/Accounts.vue') },
  { path: '/runs', name: 'runs', component: () => import('../views/Runs.vue') },
  { path: '/settings', name: 'settings', component: () => import('../views/Settings.vue') },
  { path: '/roadmap/:feature', name: 'roadmap', component: () => import('../views/EmptyStub.vue') },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
})
