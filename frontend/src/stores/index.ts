// M10-8 store 集合（真实只读 fetch + 类型化）

import { defineStore } from 'pinia'
import { ref } from 'vue'
import { message } from 'ant-design-vue'
import { api, apiPost, unwrapError, GENERATION_TIMEOUT_MS } from '../api/client'

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
  auth_type?: 'scan_qr' | 'config_file'
}

// U7-7: 一键登录 run 状态（前端轮询持有）
export interface LoginRunState {
  platform: string
  account: string
  status: 'queued' | 'running' | 'succeeded' | 'failed'
  message: string
  message_at?: string
  error_code?: string
  error_message?: string
}

export const useAccountsStore = defineStore('accounts', () => {
  const items = ref<AccountHealthItem[]>([])
  const guidance = ref<LoginGuidance[]>([])
  const loading = ref(false)
  const loaded = ref(false)
  const error = ref<string | null>(null)

  // U7-7: 一键登录 run 状态表（key = run_id）
  const runningLogins = ref<Map<string, LoginRunState>>(new Map())

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
      loaded.value = true
    } catch (e) {
      error.value = String(e)
    } finally {
      loading.value = false
    }
  }

  // U7-7: 触发一键登录 + 轮询进度
  // - POST /accounts/{platform}/{account}/login 拿 run_id
  // - 每 1.5s 轮询 GET /runs/{run_id} 拿最新 message
  // - succeeded: toast success + 刷新账号健康 + 2s 后清理
  // - failed: toast error + 5s 后清理
  // - 6 分钟兜底超时（比后端 login 5 分钟 timeout 多 1 分钟 buffer，
  //   让后端能写完 status=failed 后前端还能拿到 error_message）
  async function loginAccount(platform: string, account: string): Promise<string> {
    const POLL_MS = 1500
    const TIMEOUT_MS = 6 * 60 * 1000

    const res = await apiPost<{ run_id: string; status: string }>(
      `/accounts/${platform}/${account}/login`,
      {},
    )
    const runId = res.data.run_id
    message.info(`登录已启动：${platform}/${account}`)

    // 初始状态（响应式 Map 需要重新赋值触发更新）
    runningLogins.value.set(runId, {
      platform,
      account,
      status: 'queued',
      message: '已提交，等待开始...',
    })
    runningLogins.value = new Map(runningLogins.value)

    let pollHandle: ReturnType<typeof setInterval> | null = null
    let timeoutHandle: ReturnType<typeof setTimeout> | null = null
    let finished = false

    const cleanup = (delayMs: number) => {
      if (finished) return
      finished = true
      if (pollHandle !== null) {
        clearInterval(pollHandle)
        pollHandle = null
      }
      if (timeoutHandle !== null) {
        clearTimeout(timeoutHandle)
        timeoutHandle = null
      }
      setTimeout(() => {
        runningLogins.value.delete(runId)
        runningLogins.value = new Map(runningLogins.value)
      }, delayMs)
    }

    pollHandle = setInterval(async () => {
      try {
        const rec = await api.get<{
          status: string
          message?: string
          message_at?: string
          error?: { code: string; message: string }
          result?: { path: string }
        }>(`/runs/${runId}`)
        const cur = runningLogins.value.get(runId)
        const next: LoginRunState = {
          platform,
          account,
          status: rec.data.status as LoginRunState['status'],
          message: rec.data.message ?? cur?.message ?? '',
          message_at: rec.data.message_at,
          error_code: rec.data.error?.code,
          error_message: rec.data.error?.message,
        }
        runningLogins.value.set(runId, next)
        runningLogins.value = new Map(runningLogins.value)

        if (rec.data.status === 'succeeded') {
          message.success(`登录完成：${platform}/${account}`)
          cleanup(2000)
          await load()  // 成功后刷新账号健康
        } else if (rec.data.status === 'failed') {
          const errMsg = rec.data.error?.message ?? '登录失败'
          message.error(`登录失败：${platform}/${account}（${errMsg}）`)
          cleanup(5000)
        }
      } catch (e) {
        // 轮询失败不打断主流程（网络抖动）
        console.warn('login poll failed', e)
      }
    }, POLL_MS)

    timeoutHandle = setTimeout(() => {
      if (finished) return
      if (pollHandle !== null) {
        clearInterval(pollHandle)
        pollHandle = null
        const cur = runningLogins.value.get(runId)
        if (cur && (cur.status === 'queued' || cur.status === 'running')) {
          runningLogins.value.set(runId, {
            ...cur,
            status: 'failed',
            error_code: 'timeout',
            error_message: `轮询超时（${POLL_MS}ms × ${Math.round(TIMEOUT_MS / POLL_MS)} 次）`,
          })
          runningLogins.value = new Map(runningLogins.value)
          message.error(`登录超时：${platform}/${account}`)
          setTimeout(() => {
            runningLogins.value.delete(runId)
            runningLogins.value = new Map(runningLogins.value)
          }, 5000)
        }
      }
    }, TIMEOUT_MS)

    return runId
  }

  // U7-8: 删除已保存的登录凭据（只清凭据文件，不改 config.yaml；
  // 账号仍留在配置里，恢复到"未授权"状态，可以重新一键登录）
  async function deleteAccountCredential(platform: string, account: string): Promise<void> {
    try {
      await api.delete(`/accounts/${platform}/${account}/login`)
      message.success(`已清除登录凭据：${platform}/${account}`)
      await load()
    } catch (e) {
      message.error(`清除失败：${unwrapError(e)}`)
      throw e
    }
  }

  return {
    items,
    guidance,
    loading,
    loaded,
    error,
    runningLogins,
    load,
    loginAccount,
    deleteAccountCredential,
  }
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

export interface SettingsKeyItem {
  name: string
  set: boolean
  masked: string | null
}

export interface SettingsKeyGroup {
  group: string
  label: string
  keys: SettingsKeyItem[]
}

export const useSettingsStore = defineStore('settings', () => {
  const config = ref<Record<string, any> | null>(null)
  const doctor = ref<DoctorItem[]>([])
  const keyGroups = ref<SettingsKeyGroup[]>([])
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
  async function loadKeys() {
    try {
      const r = await api.get<{ groups: SettingsKeyGroup[] }>('/settings/keys')
      keyGroups.value = r.data.groups
    } catch (e) {
      message.error(`加载 key 状态失败：${unwrapError(e)}`)
    }
  }
  async function saveKey(name: string, value: string): Promise<boolean> {
    try {
      await api.post('/settings/keys', { name, value })
      message.success(`已保存 ${name}`)
      await Promise.all([loadKeys(), load()])
      return true
    } catch (e) {
      message.error(`保存失败：${unwrapError(e)}`)
      return false
    }
  }
  async function clearKey(name: string): Promise<boolean> {
    try {
      await api.delete(`/settings/keys/${name}`)
      message.success(`已清除 ${name}`)
      await Promise.all([loadKeys(), load()])
      return true
    } catch (e) {
      message.error(`清除失败：${unwrapError(e)}`)
      return false
    }
  }
  return { config, doctor, keyGroups, loading, error, load, loadKeys, saveKey, clearKey }
})

// ── Creation (M10 P2 阶段 A) ────────────────────────────

export const useCreationStore = defineStore('creation', () => {
  const running = ref(false)
  const lastResult = ref<ContentItem | null>(null)
  const lastError = ref<string | null>(null)

  async function run(topicId: string): Promise<ContentItem | null> {
    running.value = true
    lastError.value = null
    lastResult.value = null
    try {
      const r = await api.post<ContentItem>('/contents', { topic_id: topicId })
      lastResult.value = r.data
      return r.data
    } catch (e) {
      lastError.value = unwrapError(e)
      return null
    } finally {
      running.value = false
    }
  }

  function reset() {
    running.value = false
    lastResult.value = null
    lastError.value = null
  }

  return { running, lastResult, lastError, run, reset }
})

// ── Derivative (M10 P2 阶段 B: 单条衍生小红书) ────────────

export interface DerivativeResult {
  slides_count: number
  caption_chars: number
  tags: string[]
}

export const useDerivativeStore = defineStore('derivative', () => {
  const running = ref(false)
  const lastError = ref<string | null>(null)

  async function run(
    contentId: string,
  ): Promise<DerivativeResult | null> {
    running.value = true
    lastError.value = null
    try {
      const r = await api.post<{ derivative: DerivativeResult }>(
        `/contents/${contentId}/derivative`,
        undefined,
        { timeout: GENERATION_TIMEOUT_MS },
      )
      return r.data.derivative
    } catch (e) {
      lastError.value = unwrapError(e)
      return null
    } finally {
      running.value = false
    }
  }

  return { running, lastError, run }
})

// ── TopicAction (M10 P2 阶段 C: topics promote/reject) ──────

export const useTopicActionStore = defineStore('topic-action', () => {
  const running = ref(false)
  const lastError = ref<string | null>(null)

  async function run(
    topicId: string,
    action: 'promote' | 'reject',
  ): Promise<TopicItem | null> {
    running.value = true
    lastError.value = null
    try {
      const r = await apiPost<TopicItem>(
        `/topics/${topicId}/${action}`,
      )
      return r.data
    } catch (e) {
      lastError.value = unwrapError(e)
      return null
    } finally {
      running.value = false
    }
  }

  function reset() {
    running.value = false
    lastError.value = null
  }

  return { running, lastError, run, reset }
})

// ── ReviewAction (M10 P2 阶段 C: review approve/reject) ────

export interface ReviewActionResult {
  id: string
  status: string
  gate_verdict: string | null
}

export const useReviewActionStore = defineStore('review-action', () => {
  const running = ref(false)
  const lastError = ref<string | null>(null)

  async function run(
    contentId: string,
    decision: 'approve' | 'reject',
    reason: string = '',
  ): Promise<ReviewActionResult | null> {
    running.value = true
    lastError.value = null
    try {
      const r = await apiPost<ReviewActionResult>(
        `/review/${contentId}`,
        { decision, reason },
      )
      return r.data
    } catch (e) {
      lastError.value = unwrapError(e)
      return null
    } finally {
      running.value = false
    }
  }

  function reset() {
    running.value = false
    lastError.value = null
  }

  return { running, lastError, run, reset }
})

// ── PubAction (M10 P2 阶段 C: publications reschedule/cancel/retry) ──

export const usePubActionStore = defineStore('pub-action', () => {
  const running = ref(false)
  const lastError = ref<string | null>(null)

  async function reschedule(
    pubId: string,
    scheduledAt: string,
  ): Promise<PublicationItem | null> {
    running.value = true
    lastError.value = null
    try {
      const r = await apiPost<PublicationItem>(
        `/publications/${pubId}/reschedule`,
        { scheduled_at: scheduledAt },
      )
      return r.data
    } catch (e) {
      lastError.value = unwrapError(e)
      return null
    } finally {
      running.value = false
    }
  }

  async function cancel(pubId: string): Promise<PublicationItem | null> {
    running.value = true
    lastError.value = null
    try {
      const r = await apiPost<PublicationItem>(
        `/publications/${pubId}/cancel`,
      )
      return r.data
    } catch (e) {
      lastError.value = unwrapError(e)
      return null
    } finally {
      running.value = false
    }
  }

  async function retry(pubId: string): Promise<PublicationItem | null> {
    running.value = true
    lastError.value = null
    try {
      const r = await apiPost<PublicationItem>(
        `/publications/${pubId}/retry`,
      )
      return r.data
    } catch (e) {
      lastError.value = unwrapError(e)
      return null
    } finally {
      running.value = false
    }
  }

  function reset() {
    running.value = false
    lastError.value = null
  }

  return { running, lastError, reschedule, cancel, retry, reset }
})

// ── ImageGen (M10 P2 阶段 B: 真实 AI 出图) ──────────────

export interface ImageGenResult {
  cover_path: string
  inline_images: string[]
  cost_usd: number
}

export const useImageGenStore = defineStore('imagegen', () => {
  const running = ref(false)
  const lastError = ref<string | null>(null)

  async function run(contentId: string): Promise<ImageGenResult | null> {
    running.value = true
    lastError.value = null
    try {
      const r = await api.post<ImageGenResult>(
        `/contents/${contentId}/generate-images`,
        undefined,
        { timeout: GENERATION_TIMEOUT_MS },
      )
      return r.data
    } catch (e) {
      lastError.value = unwrapError(e)
      return null
    } finally {
      running.value = false
    }
  }

  return { running, lastError, run }
})

// ── Schedule (M10-11 阶段 D: 手动排期) ───────────────────

export interface SchedulePayload {
  platform: string
  account_id: string
  scheduled_at: string  // ISO8601
}

export const useScheduleStore = defineStore('schedule', () => {
  const running = ref(false)
  const lastResult = ref<PublicationItem | null>(null)
  const lastError = ref<string | null>(null)

  async function run(
    contentId: string,
    payload: SchedulePayload,
  ): Promise<PublicationItem | null> {
    running.value = true
    lastError.value = null
    lastResult.value = null
    try {
      const r = await apiPost<PublicationItem>(
        `/contents/${contentId}/schedule`,
        payload,
      )
      lastResult.value = r.data
      return r.data
    } catch (e) {
      lastError.value = unwrapError(e)
      return null
    } finally {
      running.value = false
    }
  }

  function reset() {
    running.value = false
    lastResult.value = null
    lastError.value = null
  }

  return { running, lastResult, lastError, run, reset }
})

// ── Preview (M10-12 阶段 E: dry-run 发布预演) ─────────────

export interface PreviewBody {
  title: string
  body_excerpt: string
  media: string[]
  tags: string[]
  platform: string
  account_id: string
  scheduled_at: string
}

export interface SafePublishPreviewResult {
  published: boolean
  reason: string
  dry_run: boolean
}

export interface PreviewResult {
  validate_passed: boolean
  validate_errors: string[]
  preview: PreviewBody
  safe_publish_result: SafePublishPreviewResult
}

export type PreviewRunStatus = 'queued' | 'succeeded' | 'failed'

export interface PreviewRun {
  run_id: string
  publication_id?: string
  status: PreviewRunStatus
  started_at?: string
  finished_at?: string
  result?: PreviewResult
  error_code?: string
  error?: string
}

const PREVIEW_POLL_INTERVAL_MS = 1_000
const PREVIEW_POLL_TIMEOUT_MS = 30_000

async function pollPreviewRun(runId: string): Promise<PreviewRun> {
  const deadline = Date.now() + PREVIEW_POLL_TIMEOUT_MS
  while (Date.now() < deadline) {
    const r = await api.get<PreviewRun>(`/runs/${runId}`)
    const data = r.data
    if (data.status === 'succeeded' || data.status === 'failed') {
      return data
    }
    await new Promise((resolve) => setTimeout(resolve, PREVIEW_POLL_INTERVAL_MS))
  }
  throw new Error(`preview run ${runId} timed out`)
}

export const usePreviewStore = defineStore('preview', () => {
  const running = ref(false)
  const lastResult = ref<PreviewResult | null>(null)
  const lastRun = ref<PreviewRun | null>(null)
  const lastError = ref<string | null>(null)

  async function run(publicationId: string): Promise<PreviewRun | null> {
    running.value = true
    lastError.value = null
    lastResult.value = null
    lastRun.value = null
    try {
      const queued = await apiPost<{ run_id: string; status: 'queued' }>(
        `/publications/${publicationId}/publish/preview`,
        {},
      )
      const run = await pollPreviewRun(queued.data.run_id)
      lastRun.value = run
      if (run.status === 'succeeded' && run.result) {
        lastResult.value = run.result
        return run
      }
      lastError.value = `${run.error_code ?? 'preview_error'}: ${run.error ?? ''}`.trim()
      return run
    } catch (e) {
      lastError.value = unwrapError(e)
      return null
    } finally {
      running.value = false
    }
  }

  function reset() {
    running.value = false
    lastResult.value = null
    lastRun.value = null
    lastError.value = null
  }

  return { running, lastResult, lastRun, lastError, run, reset }
})
