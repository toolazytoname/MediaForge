// Writer stores for the current creation path.

import { defineStore } from 'pinia'
import { ref } from 'vue'
import { message } from 'ant-design-vue'
import { api, apiPost, unwrapError, GENERATION_TIMEOUT_MS } from '../api/client'

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
  has_master?: boolean
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

  async function create(input: Pick<IdeaItem, 'input_type' | 'content'> & { title?: string }): Promise<IdeaItem> {
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

  async function compose(projectId: string): Promise<MasterDocument> {
    const response = await api.post<MasterDocument>(
      `/projects/${projectId}/compose`, {}, { timeout: GENERATION_TIMEOUT_MS },
    )
    master.value = response.data
    return response.data
  }

  async function proposeTitles(projectId: string): Promise<string[]> {
    const response = await api.post<{ titles: string[] }>(
      `/projects/${projectId}/master/titles`, {}, { timeout: GENERATION_TIMEOUT_MS },
    )
    return response.data.titles
  }

  async function applyTitle(projectId: string, title: string): Promise<MasterDocument> {
    const response = await api.post<MasterDocument>(`/projects/${projectId}/master/title`, { title })
    master.value = response.data
    return response.data
  }

  async function request(
    projectId: string,
    input: Pick<MasterSuggestion, 'action' | 'selection'> & { note?: string },
  ): Promise<MasterSuggestion> {
    const body: Record<string, string> = { action: input.action }
    if (input.selection) body.selection = input.selection
    if (input.note?.trim()) body.note = input.note.trim()
    const response = await api.post<MasterSuggestion>(
      `/projects/${projectId}/master/suggestions`,
      body,
      { timeout: GENERATION_TIMEOUT_MS },
    )
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

  return { master, suggestions, loading, error, load, save, proposeDraft, compose, proposeTitles, applyTitle, request, accept, reject, restore }
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
    const response = await api.post<VisualAsset>(`/projects/${projectId}/visuals/assets`, { slot_id: slotId, prompt }, { timeout: GENERATION_TIMEOUT_MS })
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
  const openaiImageBaseUrl = ref<string | null>(null)
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
  async function loadOpenAIImageBaseUrl() {
    try {
      const r = await api.get<{ base_url: string | null }>('/settings/openai-image-base-url')
      openaiImageBaseUrl.value = r.data.base_url
    } catch (e) {
      message.error(`加载 GPT Image 2 中转站失败：${unwrapError(e)}`)
    }
  }
  async function saveOpenAIImageBaseUrl(baseUrl: string): Promise<boolean> {
    try {
      const r = await api.post<{ base_url: string | null }>('/settings/openai-image-base-url', { base_url: baseUrl })
      openaiImageBaseUrl.value = r.data.base_url
      message.success('已保存 GPT Image 2 中转站地址')
      return true
    } catch (e) {
      message.error(`保存失败：${unwrapError(e)}`)
      return false
    }
  }
  async function clearOpenAIImageBaseUrl(): Promise<boolean> {
    try {
      const r = await api.delete<{ base_url: string | null }>('/settings/openai-image-base-url')
      openaiImageBaseUrl.value = r.data.base_url
      message.success('已恢复为 OpenAI 默认图片接口')
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
  const wechatMp = ref<{ configured: boolean; account: string; app_id_masked: string | null } | null>(null)
  async function loadWechatMp(): Promise<void> {
    try {
      const r = await api.get<{ configured: boolean; account: string; app_id_masked: string | null }>('/settings/wechat-mp')
      wechatMp.value = r.data
    } catch (e) {
      message.error(`加载公众号配置失败：${unwrapError(e)}`)
    }
  }
  async function saveWechatMp(appId: string, appSecret: string): Promise<boolean> {
    try {
      const r = await api.post<{ configured: boolean; account: string; app_id_masked: string | null }>('/settings/wechat-mp', { app_id: appId, app_secret: appSecret })
      wechatMp.value = r.data
      message.success('已保存公众号 AppID / AppSecret，只存在本机 secrets/')
      return true
    } catch (e) {
      message.error(`保存失败：${unwrapError(e)}`)
      return false
    }
  }
  async function clearWechatMp(): Promise<boolean> {
    try {
      const r = await api.delete<{ configured: boolean; account: string; app_id_masked: string | null }>('/settings/wechat-mp')
      wechatMp.value = r.data
      message.success('已清除公众号凭据')
      return true
    } catch (e) {
      message.error(`清除失败：${unwrapError(e)}`)
      return false
    }
  }
  async function probeWechatMp(): Promise<{ ok: boolean; message: string } | null> {
    try {
      const r = await api.post<{ ok: boolean; message: string }>('/settings/wechat-mp/probe', {})
      return r.data
    } catch (e) {
      message.error(`检查失败：${unwrapError(e)}`)
      return null
    }
  }
  return {
    config, doctor, keyGroups, openaiImageBaseUrl, wechatMp, loading, error,
    load, loadKeys, saveKey, clearKey,
    loadOpenAIImageBaseUrl, saveOpenAIImageBaseUrl, clearOpenAIImageBaseUrl,
    setPublishEnabled, setPublishAllowedPlatforms,
    loadWechatMp, saveWechatMp, clearWechatMp, probeWechatMp,
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
  async function prepare(projectId: string): Promise<{ variants: PlatformVariant[]; warnings: string[] }> {
    const response = await api.post<{ variants: PlatformVariant[]; warnings: string[] }>(
      `/projects/${projectId}/prepare-platforms`, {}, { timeout: GENERATION_TIMEOUT_MS },
    )
    variants.value = response.data.variants
    return response.data
  }
  async function sendWechatDraft(projectId: string): Promise<WechatDraftReceipt> {
    return (await api.post<WechatDraftReceipt>(`/projects/${projectId}/wechat-draft`, {}, { timeout: GENERATION_TIMEOUT_MS })).data
  }
  async function loadWechatDraft(projectId: string): Promise<WechatDraftReceipt | null> {
    return (await api.get<{ receipt: WechatDraftReceipt | null }>(`/projects/${projectId}/wechat-draft`)).data.receipt
  }
  return { variants, loading, error, load, create, save, lock, checkUpstream, acknowledgeMaster, restore, prepare, sendWechatDraft, loadWechatDraft }
})

export interface WechatDraftReceipt {
  project_id: string
  title: string
  media_id: string
  destination: 'wechat_draft'
  published: boolean
  sent_at: string
  message: string
}

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
