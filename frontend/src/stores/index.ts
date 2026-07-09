// M10-7 Pinia store 集合（首期只读 fetch 占位；M10-8 填充实际字段）

import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api } from '../api/client'

// 各 store 仅暴露 load() 方法；M10-8 接入真实字段

export const useDashboardStore = defineStore('dashboard', () => {
  const data = ref<any>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  async function load() {
    loading.value = true
    error.value = null
    try {
      const r = await api.get('/dashboard')
      data.value = r.data
    } catch (e) {
      error.value = String(e)
    } finally {
      loading.value = false
    }
  }
  return { data, loading, error, load }
})

export const useTopicsStore = defineStore('topics', () => {
  const items = ref<any[]>([])
  const total = ref(0)
  async function load(params: Record<string, any> = {}) {
    const r = await api.get('/topics', { params })
    items.value = r.data.items
    total.value = r.data.total
  }
  return { items, total, load }
})

export const useContentsStore = defineStore('contents', () => {
  const items = ref<any[]>([])
  const total = ref(0)
  async function load(params: Record<string, any> = {}) {
    const r = await api.get('/contents', { params })
    items.value = r.data.items
    total.value = r.data.total
  }
  return { items, total, load }
})

export const useReviewStore = defineStore('review', () => {
  const items = ref<any[]>([])
  async function load() {
    const r = await api.get('/review')
    items.value = r.data.items
  }
  return { items, load }
})

export const usePublishStore = defineStore('publish', () => {
  const calendar = ref<any>(null)
  const records = ref<any[]>([])
  async function loadCalendar(week?: string) {
    const r = await api.get('/publish/calendar', { params: week ? { week } : {} })
    calendar.value = r.data
  }
  async function loadRecords(params: Record<string, any> = {}) {
    const r = await api.get('/publish/records', { params })
    records.value = r.data.items
  }
  return { calendar, records, loadCalendar, loadRecords }
})

export const useAnalyticsStore = defineStore('analytics', () => {
  const weekly = ref<any>(null)
  const cost = ref<any>(null)
  const platforms = ref<any>(null)
  async function loadAll() {
    const [w, c, p] = await Promise.all([
      api.get('/analytics/weekly'),
      api.get('/analytics/cost', { params: { group: 'stage' } }),
      api.get('/analytics/platforms'),
    ])
    weekly.value = w.data
    cost.value = c.data
    platforms.value = p.data
  }
  return { weekly, cost, platforms, loadAll }
})

export const useAccountsStore = defineStore('accounts', () => {
  const items = ref<any[]>([])
  const guidance = ref<any[]>([])
  async function load() {
    const [a, g] = await Promise.all([
      api.get('/accounts'),
      api.get('/accounts/login-guidance'),
    ])
    items.value = a.data.items
    guidance.value = g.data.items
  }
  return { items, guidance, load }
})

export const useRunsStore = defineStore('runs', () => {
  const items = ref<any[]>([])
  const whitelist = ref<string[]>([])
  async function load() {
    const r = await api.get('/runs')
    items.value = r.data.items
    whitelist.value = r.data.stage_whitelist
  }
  return { items, whitelist, load }
})

export const useSettingsStore = defineStore('settings', () => {
  const config = ref<any>(null)
  const doctor = ref<any[]>([])
  async function load() {
    const r = await api.get('/settings')
    config.value = r.data.config
    doctor.value = r.data.doctor
  }
  return { config, doctor, load }
})
