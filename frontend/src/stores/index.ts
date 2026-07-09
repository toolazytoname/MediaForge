// M10-8 store 集合（真实只读 fetch + 类型化）

import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api } from '../api/client'

// ── Dashboard ──────────────────────────────────────────────

export interface BudgetInfo {
  monthly_usd: number
  used_usd: number
  used_ratio: number
}

export interface TodoInfo {
  to_review: number
  to_publish: number
  publish_failed: number
}

export interface GateHistogramBucket {
  score_range: string
  count: number
}

export interface ActivityItem {
  id: string
  kind: 'topic' | 'content' | 'publication'
  status: string
  updated_at: string
}

export interface DashboardData {
  counts: Record<string, Record<string, number>>
  todos: TodoInfo
  budget: BudgetInfo
  activity: ActivityItem[]
  gate_histogram: GateHistogramBucket[]
  gate_correlation: number | null
  config_error: string | null
}

export const useDashboardStore = defineStore('dashboard', () => {
  const data = ref<DashboardData | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  async function load() {
    loading.value = true
    error.value = null
    try {
      const r = await api.get<DashboardData>('/dashboard')
      data.value = r.data
    } catch (e) {
      error.value = String(e)
    } finally {
      loading.value = false
    }
  }
  return { data, loading, error, load }
})

// ── Topics ─────────────────────────────────────────────────

export interface TopicItem {
  id: string
  source: string
  title: string
  url: string | null
  summary: string | null
  content_hash: string
  pillar: string | null
  score: number | null
  score_reason: string | null
  status: string
  created_at: string
  updated_at: string
}

export interface ListResponse<T> {
  items: T[]
  total: number
  limit: number
  offset: number
}

export const useTopicsStore = defineStore('topics', () => {
  const items = ref<TopicItem[]>([])
  const total = ref(0)
  const loading = ref(false)
  const error = ref<string | null>(null)
  async function load(params: Record<string, string | number> = {}) {
    loading.value = true
    error.value = null
    try {
      const r = await api.get<ListResponse<TopicItem>>('/topics', { params })
      items.value = r.data.items
      total.value = r.data.total
    } catch (e) {
      error.value = String(e)
    } finally {
      loading.value = false
    }
  }
  return { items, total, loading, error, load }
})

// ── Contents ───────────────────────────────────────────────

export interface ContentItem extends TopicItem {
  // 公共字段
}

export interface ContentDetail extends TopicItem {
  // 详情
  canonical_path: string
  formats: string[]
  gate_score_total: number | null
  gate_scores: Record<string, number> | null
  gate_verdict: string | null
  cover_path: string | null
  inline_images: string[]
  canonical_html: string
  files: { path: string; platform: string | null; kind: string; exists: boolean; size: number }[]
  images: { cover: string | null; inline: string[] }
  publications: any[]
}

export const useContentsStore = defineStore('contents', () => {
  const items = ref<ContentItem[]>([])
  const total = ref(0)
  const loading = ref(false)
  const error = ref<string | null>(null)
  async function load(params: Record<string, string | number> = {}) {
    loading.value = true
    error.value = null
    try {
      const r = await api.get<ListResponse<ContentItem>>('/contents', { params })
      items.value = r.data.items
      total.value = r.data.total
    } catch (e) {
      error.value = String(e)
    } finally {
      loading.value = false
    }
  }
  async function getDetail(id: string): Promise<ContentDetail> {
    const r = await api.get<ContentDetail>(`/contents/${id}`)
    return r.data
  }
  return { items, total, loading, error, load, getDetail }
})

// ── Review ─────────────────────────────────────────────────

export const useReviewStore = defineStore('review', () => {
  const items = ref<ContentDetail[]>([])
  const total = ref(0)
  const loading = ref(false)
  const error = ref<string | null>(null)
  async function load() {
    loading.value = true
    error.value = null
    try {
      const r = await api.get<{ items: ContentDetail[]; total: number }>('/review')
      items.value = r.data.items
      total.value = r.data.total
    } catch (e) {
      error.value = String(e)
    } finally {
      loading.value = false
    }
  }
  return { items, total, loading, error, load }
})

// ── Publish ────────────────────────────────────────────────

export interface PublicationItem {
  id: string
  content_id: string
  platform: string
  account_id: string
  scheduled_at: string
  published_at: string | null
  platform_post_id: string | null
  platform_url: string | null
  error: string | null
  retry_count: number
  status: string
  created_at: string
  updated_at: string
  latest_metric?: { views: number; likes: number; comments: number; shares: number } | null
}

export interface CalendarDay {
  date: string
  publications: PublicationItem[]
}

export interface CalendarData {
  week_start: string
  week_end: string
  this_week: string
  prev_week: string
  next_week: string
  days: CalendarDay[]
}

export const usePublishStore = defineStore('publish', () => {
  const calendar = ref<CalendarData | null>(null)
  const records = ref<PublicationItem[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)
  async function loadCalendar(week?: string) {
    loading.value = true
    error.value = null
    try {
      const r = await api.get<CalendarData>('/publish/calendar', {
        params: week ? { week } : {},
      })
      calendar.value = r.data
    } catch (e) {
      error.value = String(e)
    } finally {
      loading.value = false
    }
  }
  async function loadRecords(params: Record<string, string | number | boolean> = {}) {
    loading.value = true
    error.value = null
    try {
      const r = await api.get<{ items: PublicationItem[] }>('/publish/records', { params })
      records.value = r.data.items
    } catch (e) {
      error.value = String(e)
    } finally {
      loading.value = false
    }
  }
  return { calendar, records, loading, error, loadCalendar, loadRecords }
})

// ── Analytics ──────────────────────────────────────────────

export interface CostItem {
  stage: string
  calls: number
  cost_usd: number
  input_tokens: number
  output_tokens: number
}

export interface DayCostItem {
  date: string
  calls: number
  cost_usd: number
}

export interface PlatformItem {
  platform: string
  publications: number
  latest_views: number
  latest_likes: number
  latest_comments: number
  latest_shares: number
}

export const useAnalyticsStore = defineStore('analytics', () => {
  const weekly = ref<any>(null)
  const cost = ref<{ group: string; items: CostItem[] | DayCostItem[] } | null>(null)
  const platforms = ref<{ items: PlatformItem[] } | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  async function loadAll() {
    loading.value = true
    error.value = null
    try {
      const [w, c, p] = await Promise.all([
        api.get('/analytics/weekly'),
        api.get('/analytics/cost', { params: { group: 'stage' } }),
        api.get('/analytics/platforms'),
      ])
      weekly.value = w.data
      cost.value = c.data
      platforms.value = p.data
    } catch (e) {
      error.value = String(e)
    } finally {
      loading.value = false
    }
  }
  return { weekly, cost, platforms, loading, error, loadAll }
})

// ── Accounts ───────────────────────────────────────────────

export interface AccountHealthItem {
  platform: string
  account: string
  healthy: boolean
  detail: string
  last_check_at: string
}

export interface LoginGuidance {
  platform: string
  command: string
  notes: string
}

export const useAccountsStore = defineStore('accounts', () => {
  const items = ref<AccountHealthItem[]>([])
  const guidance = ref<LoginGuidance[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)
  async function load() {
    loading.value = true
    error.value = null
    try {
      const [a, g] = await Promise.all([
        api.get<{ items: AccountHealthItem[] }>('/accounts'),
        api.get<{ items: LoginGuidance[] }>('/accounts/login-guidance'),
      ])
      items.value = a.data.items
      guidance.value = g.data.items
    } catch (e) {
      error.value = String(e)
    } finally {
      loading.value = false
    }
  }
  return { items, guidance, loading, error, load }
})

// ── Runs ───────────────────────────────────────────────────

export interface RunsData {
  items: any[]
  stage_whitelist: string[]
}

export const useRunsStore = defineStore('runs', () => {
  const items = ref<any[]>([])
  const whitelist = ref<string[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)
  async function load() {
    loading.value = true
    error.value = null
    try {
      const r = await api.get<RunsData>('/runs')
      items.value = r.data.items
      whitelist.value = r.data.stage_whitelist
    } catch (e) {
      error.value = String(e)
    } finally {
      loading.value = false
    }
  }
  return { items, whitelist, loading, error, load }
})

// ── Settings ───────────────────────────────────────────────

export interface DoctorItem {
  name: string
  ok: boolean
  hint: string
}

export const useSettingsStore = defineStore('settings', () => {
  const config = ref<Record<string, any> | null>(null)
  const doctor = ref<DoctorItem[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)
  async function load() {
    loading.value = true
    error.value = null
    try {
      const r = await api.get<{ config: Record<string, any>; doctor: DoctorItem[] }>('/settings')
      config.value = r.data.config
      doctor.value = r.data.doctor
    } catch (e) {
      error.value = String(e)
    } finally {
      loading.value = false
    }
  }
  return { config, doctor, loading, error, load }
})
