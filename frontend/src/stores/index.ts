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

// ── Projects ──────────────────────────────────────────────

export interface ProjectItem {
  id: string
  title: string
  idea: string
  audience: string
  goal: string
  voice: string
  autonomy: 'assist' | 'collaborate' | 'draft' | 'pack'
  content_ids: string[]
  asset_paths: string[]
  created_at: string
  updated_at: string
}

export interface ProjectInput {
  title: string
  idea: string
  audience: string
  goal: string
  voice: string
  autonomy: ProjectItem['autonomy']
}

export interface IdeaItem {
  id: string
  input_type: 'thought' | 'url' | 'text'
  content: string
  title: string
  project_id: string | null
  created_at: string
  updated_at: string
}

export interface ResearchSource {
  id: string
  title: string
  reference: string
  summary: string
  entered_at: string
  updated_at: string
}

export interface ResearchClaim {
  id: string
  text: string
  kind: 'fact' | 'judgment' | 'open_question'
  source_ids: string[]
  status: 'unverified' | 'verified' | 'open' | 'resolved'
  limitation: string | null
  counterpoint: string | null
  entered_at: string
  updated_at: string
}

export interface ResearchBoard {
  project_id: string
  sources: ResearchSource[]
  claims: ResearchClaim[]
}

export interface MasterVersion {
  version: number
  title: string
  body: string
  saved_at: string
  reason: string
}

export interface MasterDocument {
  project_id: string
  title: string
  body: string
  version: number
  created_at: string
  updated_at: string
  history: MasterVersion[]
}

export interface MasterSuggestion {
  id: string
  project_id: string
  action: 'clarify' | 'shorten' | 'change_voice' | 'add_counterpoint'
  selection: string | null
  base_version: number
  proposed_title: string
  proposed_body: string
  status: 'pending' | 'accepted' | 'rejected'
  created_at: string
  decided_at: string | null
}

export interface MasterDraftProposal { title: string; body: string }

export interface VisualSlot {
  id: string
  purpose: string
  paragraph_anchor: string | null
  direction: string
  aspect_ratio: '1:1' | '16:9' | '9:16' | '4:3' | '3:4'
}

export interface VisualAsset {
  id: string
  slot_id: string
  prompt: string
  model: string
  size: string
  version: number
  reference_asset_id: string | null
  cost_usd: number
  file_path: string | null
  status: 'candidate' | 'failed' | 'selected'
  failure: string | null
  selection_reason: string | null
  user_rating: number | null
  created_at: string
}

export interface VisualPlan {
  project_id: string
  bible: Record<string, string>
  slots: VisualSlot[]
  assets: VisualAsset[]
}

export interface VisualProviderStatus {
  available: boolean
  provider: 'openai' | null
  model: string
  reason: string | null
}

export const useProjectsStore = defineStore('projects', () => {
  const items = ref<ProjectItem[]>([])
  const total = ref(0)
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function load() {
    loading.value = true
    error.value = null
    try {
      const response = await api.get<{ items: ProjectItem[]; total: number }>('/projects')
      items.value = response.data.items
      total.value = response.data.total
    } catch (e) {
      error.value = unwrapError(e)
    } finally {
      loading.value = false
    }
  }

  async function getDetail(id: string): Promise<ProjectItem> {
    const response = await api.get<ProjectItem>(`/projects/${id}`)
    return response.data
  }

  async function create(input: ProjectInput): Promise<ProjectItem> {
    const response = await apiPost<ProjectItem>('/projects', input)
    return response.data
  }

  return { items, total, loading, error, load, getDetail, create }
})

export const useIdeasStore = defineStore('ideas', () => {
  const items = ref<IdeaItem[]>([])
  const total = ref(0)
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function load() {
    loading.value = true
    error.value = null
    try {
      const response = await api.get<{ items: IdeaItem[]; total: number }>('/ideas')
      items.value = response.data.items
      total.value = response.data.total
    } catch (e) {
      error.value = unwrapError(e)
    } finally {
      loading.value = false
    }
  }

  async function create(input: Pick<IdeaItem, 'input_type' | 'content' | 'title'>): Promise<IdeaItem> {
    const response = await apiPost<IdeaItem>('/ideas', input)
    return response.data
  }

  async function promote(id: string, input: Omit<ProjectInput, 'idea'>): Promise<{ idea: IdeaItem; project: ProjectItem }> {
    const response = await apiPost<{ idea: IdeaItem; project: ProjectItem }>(`/ideas/${id}/promote-to-project`, input)
    return response.data
  }

  return { items, total, loading, error, load, create, promote }
})

export const useResearchStore = defineStore('research', () => {
  const board = ref<ResearchBoard | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function load(projectId: string): Promise<void> {
    loading.value = true
    error.value = null
    try {
      board.value = (await api.get<ResearchBoard>(`/projects/${projectId}/research`)).data
    } catch (e) {
      error.value = unwrapError(e)
    } finally {
      loading.value = false
    }
  }

  async function addSource(projectId: string, input: Pick<ResearchSource, 'title' | 'reference' | 'summary'>): Promise<ResearchSource> {
    const source = (await apiPost<ResearchSource>(`/projects/${projectId}/research/sources`, input)).data
    if (board.value?.project_id === projectId) board.value = { ...board.value, sources: [...board.value.sources, source] }
    return source
  }

  async function addClaim(projectId: string, input: Omit<ResearchClaim, 'id' | 'entered_at' | 'updated_at'>): Promise<ResearchClaim> {
    const claim = (await apiPost<ResearchClaim>(`/projects/${projectId}/research/claims`, input)).data
    if (board.value?.project_id === projectId) board.value = { ...board.value, claims: [...board.value.claims, claim] }
    return claim
  }

  return { board, loading, error, load, addSource, addClaim }
})

export const useMasterStore = defineStore('master', () => {
  const master = ref<MasterDocument | null>(null)
  const suggestions = ref<MasterSuggestion[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function load(projectId: string): Promise<void> {
    loading.value = true
    error.value = null
    try {
      master.value = (await api.get<{ master: MasterDocument | null }>(`/projects/${projectId}/master`)).data.master
      suggestions.value = (await api.get<{ items: MasterSuggestion[] }>(`/projects/${projectId}/master/suggestions`)).data.items
    } catch (e) { error.value = unwrapError(e) } finally { loading.value = false }
  }

  async function save(projectId: string, input: Pick<MasterDocument, 'title' | 'body'>): Promise<MasterDocument> {
    const response = await api.put<MasterDocument>(`/projects/${projectId}/master`, input)
    master.value = response.data
    return response.data
  }

  async function proposeDraft(projectId: string): Promise<MasterDraftProposal> {
    return (await api.post<MasterDraftProposal>(
      `/projects/${projectId}/master/draft`, {}, { timeout: GENERATION_TIMEOUT_MS },
    )).data
  }

  async function request(projectId: string, input: Pick<MasterSuggestion, 'action' | 'selection'>): Promise<MasterSuggestion> {
    const response = await apiPost<MasterSuggestion>(`/projects/${projectId}/master/suggestions`, input.selection ? input : { action: input.action })
    suggestions.value = [...suggestions.value, response.data]
    return response.data
  }

  async function accept(projectId: string, suggestionId: string): Promise<MasterDocument> {
    const response = await apiPost<MasterDocument>(`/projects/${projectId}/master/suggestions/${suggestionId}/accept`, {})
    master.value = response.data
    suggestions.value = suggestions.value.map(item => item.id === suggestionId ? { ...item, status: 'accepted', decided_at: response.data.updated_at } : item)
    return response.data
  }

  async function reject(projectId: string, suggestionId: string): Promise<MasterSuggestion> {
    const response = await apiPost<MasterSuggestion>(`/projects/${projectId}/master/suggestions/${suggestionId}/reject`, {})
    suggestions.value = suggestions.value.map(item => item.id === suggestionId ? response.data : item)
    return response.data
  }

  async function restore(projectId: string, version: number): Promise<MasterDocument> {
    const response = await apiPost<MasterDocument>(`/projects/${projectId}/master/versions/${version}/restore`, {})
    master.value = response.data
    return response.data
  }

  return { master, suggestions, loading, error, load, save, proposeDraft, request, accept, reject, restore }
})

export const useVisualsStore = defineStore('visuals', () => {
  const plan = ref<VisualPlan | null>(null)
  const provider = ref<VisualProviderStatus | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function load(projectId: string): Promise<void> {
    loading.value = true; error.value = null
    try {
      const [planResponse, providerResponse] = await Promise.all([
        api.get<VisualPlan>(`/projects/${projectId}/visuals`),
        api.get<VisualProviderStatus>(`/projects/${projectId}/visuals/provider`),
      ])
      plan.value = planResponse.data
      provider.value = providerResponse.data
    }
    catch (e) { error.value = unwrapError(e) } finally { loading.value = false }
  }
  async function save(projectId: string, input: Pick<VisualPlan, 'bible' | 'slots'>): Promise<VisualPlan> {
    const response = await api.put<VisualPlan>(`/projects/${projectId}/visuals`, input)
    plan.value = response.data
    return response.data
  }
  async function generate(projectId: string, slotId: string, prompt: string): Promise<VisualAsset> {
    const response = await apiPost<VisualAsset>(`/projects/${projectId}/visuals/assets`, { slot_id: slotId, prompt })
    if (plan.value?.project_id === projectId) plan.value = { ...plan.value, assets: [...plan.value.assets, response.data] }
    return response.data
  }
  async function edit(projectId: string, slotId: string, prompt: string, referenceAssetId: string): Promise<VisualAsset> {
    const response = await apiPost<VisualAsset>(`/projects/${projectId}/visuals/assets/edit`, { slot_id: slotId, prompt, reference_asset_id: referenceAssetId })
    if (plan.value?.project_id === projectId) plan.value = { ...plan.value, assets: [...plan.value.assets, response.data] }
    return response.data
  }
  async function importPng(projectId: string, slotId: string, prompt: string, file: File): Promise<VisualAsset> {
    const dataBase64 = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader()
      reader.onerror = () => reject(reader.error ?? new Error('读取 PNG 失败'))
      reader.onload = () => resolve(String(reader.result).split(',', 2)[1] ?? '')
      reader.readAsDataURL(file)
    })
    const response = await api.post<VisualAsset>(
      `/projects/${projectId}/visuals/assets/import`,
      { slot_id: slotId, prompt, file_name: file.name, data_base64: dataBase64 },
      { timeout: GENERATION_TIMEOUT_MS },
    )
    if (plan.value?.project_id === projectId) plan.value = { ...plan.value, assets: [...plan.value.assets, response.data] }
    return response.data
  }
  async function select(projectId: string, assetId: string, reason: string, rating?: number): Promise<VisualAsset> {
    const response = await apiPost<VisualAsset>(`/projects/${projectId}/visuals/assets/${assetId}/select`, rating ? { reason, rating } : { reason })
    if (plan.value?.project_id === projectId) plan.value = { ...plan.value, assets: plan.value.assets.map(item => item.id === assetId ? response.data : item.slot_id === response.data.slot_id && item.status === 'selected' ? { ...item, status: 'candidate' } : item) }
    return response.data
  }
  return { plan, provider, loading, error, load, save, generate, edit, importPng, select }
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
  publications: PublicationItem[]
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
  // 发布总开关（用户明确要求可从 UI 操作，不必手改 config.yaml）
  async function setPublishEnabled(enabled: boolean): Promise<boolean> {
    try {
      await api.post('/settings/publish-enabled', { enabled })
      message.success(enabled ? '已开启真实发布' : '已关闭真实发布')
      await load()
      return true
    } catch (e) {
      message.error(`保存失败：${unwrapError(e)}`)
      return false
    }
  }
  async function setPublishAllowedPlatforms(platforms: string[]): Promise<boolean> {
    try {
      await api.post('/settings/publish-allowed-platforms', { platforms })
      message.success('已保存平台白名单')
      await load()
      return true
    } catch (e) {
      message.error(`保存失败：${unwrapError(e)}`)
      return false
    }
  }
  return {
    config, doctor, keyGroups, loading, error,
    load, loadKeys, saveKey, clearKey,
    setPublishEnabled, setPublishAllowedPlatforms,
  }
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

// ── RealPublish (M10 Phase D: UI「立即发布」——真实 safe_publish dry_run=False） ──

export interface RealPublishResult {
  published: boolean
  reason: string
  platform_post_id: string | null
  url: string | null
}

export type RealPublishRunStatus = 'queued' | 'succeeded' | 'failed'

export interface RealPublishRun {
  run_id: string
  publication_id?: string
  status: RealPublishRunStatus
  started_at?: string
  finished_at?: string
  result?: RealPublishResult
  error_code?: string
  error?: string
}

const REAL_PUBLISH_POLL_INTERVAL_MS = 1_000
const REAL_PUBLISH_POLL_TIMEOUT_MS = 30_000

async function pollRealPublishRun(runId: string): Promise<RealPublishRun> {
  const deadline = Date.now() + REAL_PUBLISH_POLL_TIMEOUT_MS
  while (Date.now() < deadline) {
    const r = await api.get<RealPublishRun>(`/runs/${runId}`)
    const data = r.data
    if (data.status === 'succeeded' || data.status === 'failed') {
      return data
    }
    await new Promise((resolve) => setTimeout(resolve, REAL_PUBLISH_POLL_INTERVAL_MS))
  }
  throw new Error(`real publish run ${runId} timed out`)
}

export const useRealPublishStore = defineStore('realPublish', () => {
  const running = ref(false)
  const lastResult = ref<RealPublishResult | null>(null)
  const lastRun = ref<RealPublishRun | null>(null)
  const lastError = ref<string | null>(null)

  async function run(publicationId: string): Promise<RealPublishRun | null> {
    running.value = true
    lastError.value = null
    lastResult.value = null
    lastRun.value = null
    try {
      const queued = await apiPost<{ run_id: string; status: 'queued' }>(
        `/publications/${publicationId}/publish`,
        {},
      )
      const run = await pollRealPublishRun(queued.data.run_id)
      lastRun.value = run
      if (run.status === 'succeeded' && run.result) {
        lastResult.value = run.result
        return run
      }
      lastError.value = `${run.error_code ?? 'publish_error'}: ${run.error ?? ''}`.trim()
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

// ── VideoCreation (M12-3: 视频创作向导——脚本派生 + 提交/轮询) ──

export type VideoEngineName = 'mpt' | 'pixelle' | 'digitalhuman'
export type VideoAspect = '9:16' | '16:9'

export interface VideoJobResult {
  job_id: string
  content_id: string
  engine: VideoEngineName
  state: string
  progress: number | null
  error: string | null
  output_path: string | null
  output_url: string | null
  created_at: string
  updated_at: string
}

const VIDEO_POLL_INTERVAL_MS = 3_000
const VIDEO_TERMINAL_STATES = new Set(['done', 'failed'])

export const useVideoCreationStore = defineStore('video-creation', () => {
  const script = ref('')
  const engine = ref<VideoEngineName | null>(null)
  const aspect = ref<VideoAspect>('9:16')
  const style = ref<Record<string, any>>({})
  const job = ref<VideoJobResult | null>(null)
  const running = ref(false)
  const polling = ref(false)
  const lastError = ref<string | null>(null)

  let pollTimer: ReturnType<typeof setTimeout> | null = null

  function stopPolling() {
    if (pollTimer !== null) {
      clearTimeout(pollTimer)
      pollTimer = null
    }
    polling.value = false
  }

  async function deriveScript(contentId: string, durationS: number): Promise<string | null> {
    running.value = true
    lastError.value = null
    try {
      const r = await api.post<{ script: string }>(
        `/contents/${contentId}/video-script`,
        { duration_s: durationS },
        { timeout: GENERATION_TIMEOUT_MS },
      )
      script.value = r.data.script
      return r.data.script
    } catch (e) {
      lastError.value = unwrapError(e)
      return null
    } finally {
      running.value = false
    }
  }

  async function submit(
    contentId: string,
    durationS: number,
  ): Promise<VideoJobResult | null> {
    if (!engine.value) {
      lastError.value = '请先选择创作类型'
      return null
    }
    running.value = true
    lastError.value = null
    job.value = null
    try {
      const r = await apiPost<VideoJobResult>('/video-jobs', {
        content_id: contentId,
        engine: engine.value,
        script: script.value,
        duration_s: durationS,
        aspect: aspect.value,
        style: style.value,
      })
      job.value = r.data
      return r.data
    } catch (e) {
      lastError.value = unwrapError(e)
      return null
    } finally {
      running.value = false
    }
  }

  async function pollOnce(): Promise<VideoJobResult | null> {
    if (!job.value) return null
    try {
      const r = await api.get<VideoJobResult>(`/video-jobs/${job.value.job_id}`)
      job.value = r.data
      return r.data
    } catch (e) {
      lastError.value = unwrapError(e)
      return null
    }
  }

  // 轮询采用「await 后再 setTimeout 排下一次」的顺序模式（同 usePreviewStore
  // 的 pollPreviewRun），不是层层嵌套的 recursive setTimeout 栈；到终态或
  // 出错即停，避免无限轮询。
  function startPolling(): void {
    if (polling.value) return
    polling.value = true

    const tick = async () => {
      if (!polling.value) return
      const result = await pollOnce()
      if (!polling.value) return
      if (result === null || VIDEO_TERMINAL_STATES.has(result.state)) {
        stopPolling()
        return
      }
      pollTimer = setTimeout(tick, VIDEO_POLL_INTERVAL_MS)
    }

    void tick()
  }

  function reset() {
    stopPolling()
    script.value = ''
    engine.value = null
    aspect.value = '9:16'
    style.value = {}
    job.value = null
    running.value = false
    lastError.value = null
  }

  return {
    script,
    engine,
    aspect,
    style,
    job,
    running,
    polling,
    lastError,
    deriveScript,
    submit,
    pollOnce,
    startPolling,
    stopPolling,
    reset,
  }
})

export interface PlatformVariantVersion { version: number; title: string; summary: string; body: string; asset_ids: string[]; saved_at: string; reason: string }
export interface PlatformVariant { platform: 'wechat_mp' | 'toutiao'; title: string; summary: string; body: string; asset_ids: string[]; source_master_version: number; version: number; locked: boolean; manually_modified: boolean; upstream_updated: boolean; created_at: string; updated_at: string; history: PlatformVariantVersion[] }
export interface VariantSet { project_id: string; variants: PlatformVariant[] }

export const useVariantsStore = defineStore('variants', () => {
  const variants = ref<PlatformVariant[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)
  async function load(projectId: string) { loading.value = true; error.value = null; try { variants.value = (await api.get<VariantSet>(`/projects/${projectId}/variants`)).data.variants } catch (e) { error.value = unwrapError(e) } finally { loading.value = false } }
  async function create(projectId: string, platform: PlatformVariant['platform'], adaptWithAi = false) { const item = (await api.post<PlatformVariant>(`/projects/${projectId}/variants/${platform}`, adaptWithAi ? { adapt_with_ai: true } : undefined, { timeout: GENERATION_TIMEOUT_MS })).data; variants.value = variants.value.some(x => x.platform === platform) ? variants.value.map(x => x.platform === platform ? item : x) : [...variants.value, item]; return item }
  async function save(projectId: string, platform: PlatformVariant['platform'], input: Pick<PlatformVariant, 'title' | 'summary' | 'body' | 'asset_ids'>) { const item = (await api.put<PlatformVariant>(`/projects/${projectId}/variants/${platform}`, input)).data; variants.value = variants.value.map(x => x.platform === platform ? item : x); return item }
  async function lock(projectId: string, platform: PlatformVariant['platform'], locked: boolean) { const item = (await apiPost<PlatformVariant>(`/projects/${projectId}/variants/${platform}/lock`, { locked })).data; variants.value = variants.value.map(x => x.platform === platform ? item : x); return item }
  async function checkUpstream(projectId: string, platform: PlatformVariant['platform']) { const item = (await apiPost<PlatformVariant>(`/projects/${projectId}/variants/${platform}/check-upstream`, {})).data; variants.value = variants.value.map(x => x.platform === platform ? item : x); return item }
  async function acknowledgeMaster(projectId: string, platform: PlatformVariant['platform']) { const item = (await apiPost<PlatformVariant>(`/projects/${projectId}/variants/${platform}/acknowledge-master`, {})).data; variants.value = variants.value.map(x => x.platform === platform ? item : x); return item }
  async function restore(projectId: string, platform: PlatformVariant['platform'], version: number) { const item = (await apiPost<PlatformVariant>(`/projects/${projectId}/variants/${platform}/versions/${version}/restore`, {})).data; variants.value = variants.value.map(x => x.platform === platform ? item : x); return item }
  return { variants, loading, error, load, create, save, lock, checkUpstream, acknowledgeMaster, restore }
})

export interface ApprovalCheck { id: 'master' | 'visuals' | 'wechat_mp' | 'toutiao'; status: 'pending' | 'approved'; note: string | null; approved_by: string | null; approved_at: string | null }
export interface ApprovalEvent { action: 'rechecked' | 'approved' | 'revoked'; check_id: ApprovalCheck['id'] | null; note: string | null; actor: string; at: string }
export interface ApprovalStatus { approval: { project_id: string; snapshot: unknown | null; checks: ApprovalCheck[]; history: ApprovalEvent[] }; ready: boolean; stale: boolean; blockers: string[]; complete: boolean }
export interface ProjectExportResult { project_id: string; file_name: string; path: string; url: string }
export const useApprovalsStore = defineStore('approvals', () => {
  const status = ref<ApprovalStatus | null>(null); const loading = ref(false); const error = ref<string | null>(null)
  async function load(projectId: string) { loading.value = true; error.value = null; try { status.value = (await api.get<ApprovalStatus>(`/projects/${projectId}/approval`)).data } catch (e) { error.value = unwrapError(e) } finally { loading.value = false } }
  async function recheck(projectId: string, actor: string) { status.value = (await apiPost<ApprovalStatus>(`/projects/${projectId}/approval/recheck`, { actor })).data; return status.value }
  async function decide(projectId: string, checkId: ApprovalCheck['id'], approved: boolean, actor: string, note?: string) { status.value = (await apiPost<ApprovalStatus>(`/projects/${projectId}/approval/checks/${checkId}`, { approved, actor, ...(note ? { note } : {}) })).data; return status.value }
  async function exportPackage(projectId: string) { return (await apiPost<ProjectExportResult>(`/projects/${projectId}/export`, {})).data }
  return { status, loading, error, load, recheck, decide, exportPackage }
})
