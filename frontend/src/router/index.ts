import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

const later = () => import('../views/Later.vue')

const routes: RouteRecordRaw[] = [
  { path: '/', name: 'today', component: () => import('../views/Today.vue') },
  { path: '/ideas', name: 'ideas', component: () => import('../views/Ideas.vue') },
  { path: '/projects', name: 'projects', component: () => import('../views/Projects.vue') },
  { path: '/projects/new', name: 'project-create', component: () => import('../views/ProjectCreate.vue') },
  { path: '/projects/:id', name: 'project-detail', component: () => import('../views/Projects.vue') },
  { path: '/settings', name: 'settings', component: () => import('../views/Settings.vue') },
  { path: '/publish', name: 'publish', component: later },
  { path: '/publish/:pathMatch(.*)*', name: 'publish-legacy', component: later },
  { path: '/analytics', name: 'analytics', component: later },
  { path: '/roadmap/:feature', name: 'roadmap', component: later },
  { path: '/topics', name: 'topics', component: later },
  { path: '/contents/:pathMatch(.*)*', name: 'contents-legacy', component: later },
  { path: '/contents', name: 'contents', component: later },
  { path: '/review', name: 'review', component: later },
  { path: '/creation/:pathMatch(.*)*', name: 'creation-legacy', component: later },
  { path: '/creation', name: 'creation', component: later },
  { path: '/accounts', name: 'accounts', component: later },
  { path: '/runs', name: 'runs', component: later },
]

export const router = createRouter({
  history: createWebHistory(),
  routes,
})
