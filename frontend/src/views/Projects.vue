<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import { ArrowLeftOutlined, ArrowRightOutlined, FolderOpenOutlined } from '@ant-design/icons-vue'
import { useProjectsStore, useResearchStore, useMasterStore, useVisualsStore, useVariantsStore, useApprovalsStore, type ProjectItem, type ResearchClaim, type MasterSuggestion, type MasterDraftProposal, type VisualSlot, type VisualAsset, type PlatformVariant, type ApprovalCheck, type ProjectExportResult } from '../stores'
import { api, unwrapError } from '../api/client'
import { formatDateTime } from '../utils/format'

const route = useRoute()
const router = useRouter()
const store = useProjectsStore()
const researchStore = useResearchStore()
const masterStore = useMasterStore()
const visualsStore = useVisualsStore()
const variantsStore = useVariantsStore()
const approvalsStore = useApprovalsStore()
const { items, total, loading, error } = storeToRefs(store)
const { board, loading: researchLoading, error: researchError } = storeToRefs(researchStore)
const { master, suggestions, loading: masterLoading, error: masterError } = storeToRefs(masterStore)
const { plan: visualPlan, provider: visualProvider, loading: visualsLoading, error: visualsError } = storeToRefs(visualsStore)
const { variants, loading: variantsLoading, error: variantsError } = storeToRefs(variantsStore)
const { status: approvalStatus, loading: approvalsLoading, error: approvalsError } = storeToRefs(approvalsStore)
const project = ref<ProjectItem | null>(null)
const detailError = ref<string | null>(null)
const projectId = computed(() => typeof route.params.id === 'string' ? route.params.id : null)
const sourceForm = ref({ title: '', reference: '', summary: '' })
const claimForm = ref<{ text: string; kind: ResearchClaim['kind']; status: ResearchClaim['status']; source_ids: string[]; limitation: string; counterpoint: string }>({
  text: '', kind: 'fact', status: 'unverified', source_ids: [], limitation: '', counterpoint: '',
})
const sourceSaving = ref(false)
const claimSaving = ref(false)
const masterSaving = ref(false)
const suggestionSaving = ref(false)
const masterForm = ref({ title: '', body: '' })
const selectedText = ref('')
const visualSaving = ref(false)
const visualGenerating = ref<string | null>(null)
const visualBible = ref('')
const visualSlots = ref<VisualSlot[]>([])
const visualPrompts = ref<Record<string, string>>({})
const variantForms = ref<Record<string, { title: string; summary: string; body: string }>>({})
const approvalNotes = ref<Record<string, string>>({})
const approvalActor = ref('本机创作者')
const activeWorkbench = ref<'research' | 'master' | 'visuals' | 'variants' | 'approval'>('research')
const draftGenerating = ref(false)
const draftProposal = ref<MasterDraftProposal | null>(null)
const importingSlot = ref<string | null>(null)
const variantGenerating = ref<string | null>(null)
const exporting = ref(false)
const exportResult = ref<ProjectExportResult | null>(null)
interface ProjectMaterial { id: string; kind: string; source: string; original_name: string | null; status: string; error: string | null; analysis: { status: 'used' | 'not_used'; error: string | null; parsed_at: string; segments: Array<{ citation: string; text: string; kind: string }> } | null }
const materials = ref<ProjectMaterial[]>([])
const parsingMaterial = ref<string | null>(null)

const unverifiedFacts = computed(() => board.value?.claims.filter(item => item.kind === 'fact' && (item.status === 'unverified' || !item.source_ids.length)) ?? [])
const openQuestions = computed(() => board.value?.claims.filter(item => item.kind === 'open_question' && item.status === 'open') ?? [])
const claimStatusOptions = computed(() => claimForm.value.kind === 'open_question'
  ? [{ value: 'open', label: '待解决' }, { value: 'resolved', label: '已解决' }]
  : [{ value: 'unverified', label: '待核查' }, { value: 'verified', label: '已核查' }])
const researchReady = computed(() => (board.value?.sources.length ?? 0) >= 3
  && (board.value?.claims.some(item => item.kind === 'judgment') ?? false)
  && !unverifiedFacts.value.length && !openQuestions.value.length)
const masterReady = computed(() => (master.value?.body.trim().length ?? 0) >= 800)
const visualsReady = computed(() => (visualPlan.value?.slots.length ?? 0) >= 3
  && (visualPlan.value?.slots.every(slot => visualPlan.value?.assets.some(asset => asset.slot_id === slot.id && asset.status === 'selected')) ?? false))
const variantsReady = computed(() => ['wechat_mp', 'toutiao'].every(platform => {
  const item = variants.value.find(candidate => candidate.platform === platform)
  return Boolean(item && master.value && item.source_master_version === master.value.version && item.body.trim().length >= 600 && item.locked)
}))
const workflowSteps = computed(() => [
  { key: 'research' as const, label: '1 研究', done: researchReady.value },
  { key: 'master' as const, label: '2 主稿', done: masterReady.value },
  { key: 'visuals' as const, label: '3 视觉', done: visualsReady.value },
  { key: 'variants' as const, label: '4 双平台', done: variantsReady.value },
  { key: 'approval' as const, label: '5 审批导出', done: Boolean(approvalStatus.value?.complete && approvalStatus.value.ready && !approvalStatus.value.stale) },
])
const nextStep = computed(() => workflowSteps.value.find(item => !item.done) ?? {
  key: 'approval' as const, label: '5 审批导出', done: true,
})

async function loadPage(): Promise<void> {
  detailError.value = null
  project.value = null
  if (!projectId.value) {
    await store.load()
    return
  }
  try {
    project.value = await store.getDetail(projectId.value)
    materials.value = (await api.get<{ items: ProjectMaterial[] }>(`/projects/${projectId.value}/materials`)).data.items
    await researchStore.load(projectId.value)
    await masterStore.load(projectId.value)
    await visualsStore.load(projectId.value)
    await variantsStore.load(projectId.value)
    await approvalsStore.load(projectId.value)
    if (master.value) masterForm.value = { title: master.value.title, body: master.value.body }
    visualBible.value = Object.entries(visualPlan.value?.bible ?? {}).map(([key, value]) => `${key}: ${value}`).join('\n')
    visualSlots.value = visualPlan.value?.slots.map(slot => ({ ...slot })) ?? []
    variantForms.value = Object.fromEntries(variants.value.map(item => [item.platform, { title: item.title, summary: item.summary, body: item.body }]))
    activeWorkbench.value = workflowSteps.value.find(item => !item.done)?.key ?? 'approval'
  } catch (e) {
    detailError.value = unwrapError(e)
  }
}

async function parseMaterial(item: ProjectMaterial): Promise<void> {
  if (!projectId.value || parsingMaterial.value) return
  parsingMaterial.value = item.id
  try {
    await api.post(`/projects/${projectId.value}/materials/${item.id}/parse`)
    materials.value = (await api.get<{ items: ProjectMaterial[] }>(`/projects/${projectId.value}/materials`)).data.items
  } catch (e) { detailError.value = unwrapError(e) } finally { parsingMaterial.value = null }
}

async function refreshApprovalStatus(): Promise<void> {
  if (projectId.value) await approvalsStore.load(projectId.value)
}

async function saveMaster(): Promise<void> {
  if (!projectId.value) return
  masterSaving.value = true
  try { await masterStore.save(projectId.value, masterForm.value); await refreshApprovalStatus() } catch (e) { detailError.value = unwrapError(e) } finally { masterSaving.value = false }
}
async function proposeDraft(): Promise<void> {
  if (!projectId.value) return
  draftGenerating.value = true; detailError.value = null
  try { draftProposal.value = await masterStore.proposeDraft(projectId.value) }
  catch (e) { detailError.value = unwrapError(e) }
  finally { draftGenerating.value = false }
}
function useDraftProposal(): void {
  if (!draftProposal.value) return
  masterForm.value = { ...draftProposal.value }
  draftProposal.value = null
}
function captureSelection(): void { selectedText.value = document.getSelection()?.toString().trim() ?? '' }
async function requestSuggestion(action: MasterSuggestion['action']): Promise<void> {
  if (!projectId.value || !master.value) return
  suggestionSaving.value = true
  try { await masterStore.request(projectId.value, { action, selection: selectedText.value || null }) } catch (e) { detailError.value = unwrapError(e) } finally { suggestionSaving.value = false }
}
async function acceptSuggestion(suggestion: MasterSuggestion): Promise<void> {
  if (!projectId.value) return
  try { const updated = await masterStore.accept(projectId.value, suggestion.id); masterForm.value = { title: updated.title, body: updated.body }; await refreshApprovalStatus() } catch (e) { detailError.value = unwrapError(e) }
}
async function rejectSuggestion(suggestion: MasterSuggestion): Promise<void> {
  if (!projectId.value) return
  try { await masterStore.reject(projectId.value, suggestion.id) } catch (e) { detailError.value = unwrapError(e) }
}
async function restoreVersion(version: number): Promise<void> {
  if (!projectId.value) return
  try { const updated = await masterStore.restore(projectId.value, version); masterForm.value = { title: updated.title, body: updated.body }; await refreshApprovalStatus() } catch (e) { detailError.value = unwrapError(e) }
}

function addVisualSlot(): void {
  visualSlots.value.push({ id: `vsl_${Date.now().toString(36)}`, purpose: '正文插图', paragraph_anchor: null, direction: '围绕主稿核心观点的编辑插画，保留必要留白。', aspect_ratio: '16:9' })
}
function setupStandardVisuals(): void {
  const stamp = Date.now().toString(36)
  visualSlots.value = [
    { id: `vsl_cover_${stamp}`, purpose: '封面', paragraph_anchor: null, direction: '用一个有冲突感的核心隐喻呈现文章主张，画面简洁并预留标题空间。', aspect_ratio: '16:9' },
    { id: `vsl_open_${stamp}`, purpose: '正文插图一', paragraph_anchor: '问题', direction: '把开头的问题转化为一幅易懂的编辑插画。', aspect_ratio: '16:9' },
    { id: `vsl_thesis_${stamp}`, purpose: '正文插图二', paragraph_anchor: '主张', direction: '把文章的核心框架转化为视觉隐喻，避免文字堆叠。', aspect_ratio: '16:9' },
  ]
}
function bibleMap(): Record<string, string> {
  return visualBible.value.split('\n').reduce<Record<string, string>>((result, line) => {
    const [key, ...rest] = line.split(':'); const value = rest.join(':').trim()
    if (key?.trim() && value) result[key.trim()] = value
    return result
  }, {})
}
async function saveVisualPlan(): Promise<void> {
  if (!projectId.value) return
  const bible = bibleMap()
  if (!Object.keys(bible).length) {
    detailError.value = '请先写下至少一条视觉圣经，例如“风格: 克制的编辑插画”。'
    return
  }
  if (visualSlots.value.some(slot => !slot.purpose.trim() || !slot.direction.trim())) {
    detailError.value = '每个视觉槽位都需要用途和画面方向，才能保存或生成候选。'
    return
  }
  visualSaving.value = true
  try { await visualsStore.save(projectId.value, { bible, slots: visualSlots.value }); await refreshApprovalStatus() } catch (e) { detailError.value = unwrapError(e) } finally { visualSaving.value = false }
}
async function generateVisual(slot: VisualSlot, referenceAssetId?: string): Promise<void> {
  if (!projectId.value || !visualPrompts.value[slot.id]?.trim()) return
  visualGenerating.value = slot.id
  try {
    if (referenceAssetId) await visualsStore.edit(projectId.value, slot.id, visualPrompts.value[slot.id], referenceAssetId)
    else await visualsStore.generate(projectId.value, slot.id, visualPrompts.value[slot.id])
  } catch (e) { detailError.value = unwrapError(e) } finally { visualGenerating.value = null }
}
async function selectVisual(assetId: string): Promise<void> {
  if (!projectId.value) return
  try { await visualsStore.select(projectId.value, assetId, '适合本次主稿的视觉意图', 4); await refreshApprovalStatus() } catch (e) { detailError.value = unwrapError(e) }
}
function visualAssetUrl(asset: VisualAsset): string | null {
  return projectId.value && asset.file_path ? `/output/projects/${projectId.value}/${asset.file_path}` : null
}
async function importVisual(event: Event, slot: VisualSlot): Promise<void> {
  if (!projectId.value) return
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  importingSlot.value = slot.id
  try { await visualsStore.importPng(projectId.value, slot.id, visualPrompts.value[slot.id]?.trim() || slot.direction, file) }
  catch (e) { detailError.value = unwrapError(e) }
  finally { importingSlot.value = null; input.value = '' }
}
function platformName(platform: PlatformVariant['platform']): string { return platform === 'wechat_mp' ? '微信公众号' : '今日头条' }
function variantForm(platform: PlatformVariant['platform']): { title: string; summary: string; body: string } { return variantForms.value[platform] ?? { title: '', summary: '', body: '' } }
async function createVariant(platform: PlatformVariant['platform'], adaptWithAi = false): Promise<void> { if (!projectId.value) return; variantGenerating.value = platform; try { const item = await variantsStore.create(projectId.value, platform, adaptWithAi); variantForms.value[item.platform] = { title: item.title, summary: item.summary, body: item.body }; await refreshApprovalStatus() } catch (e) { detailError.value = unwrapError(e) } finally { variantGenerating.value = null } }
async function saveVariant(item: PlatformVariant): Promise<void> { if (!projectId.value) return; try { const form = variantForm(item.platform); await variantsStore.save(projectId.value, item.platform, { ...form, asset_ids: item.asset_ids }); await refreshApprovalStatus() } catch (e) { detailError.value = unwrapError(e) } }
async function toggleLock(item: PlatformVariant): Promise<void> { if (!projectId.value) return; try { await variantsStore.lock(projectId.value, item.platform, !item.locked); await refreshApprovalStatus() } catch (e) { detailError.value = unwrapError(e) } }
async function checkVariantUpstream(item: PlatformVariant): Promise<void> { if (!projectId.value) return; try { await variantsStore.checkUpstream(projectId.value, item.platform) } catch (e) { detailError.value = unwrapError(e) } }
async function acknowledgeVariantMaster(item: PlatformVariant): Promise<void> { if (!projectId.value) return; try { const updated = await variantsStore.acknowledgeMaster(projectId.value, item.platform); variantForms.value[item.platform] = { title: updated.title, summary: updated.summary, body: updated.body }; await refreshApprovalStatus() } catch (e) { detailError.value = unwrapError(e) } }
async function restoreVariant(item: PlatformVariant, version: number): Promise<void> { if (!projectId.value) return; try { const updated = await variantsStore.restore(projectId.value, item.platform, version); variantForms.value[item.platform] = { title: updated.title, summary: updated.summary, body: updated.body }; await refreshApprovalStatus() } catch (e) { detailError.value = unwrapError(e) } }
function previewVariant(item: PlatformVariant): void { if (!projectId.value) return; window.open(`/api/v1/projects/${projectId.value}/variants/${item.platform}/preview`, '_blank', 'noopener') }
const approvalLabel: Record<ApprovalCheck['id'], string> = { master: '主稿内容与事实边界', visuals: '已选封面与插图', wechat_mp: '微信公众号版本', toutiao: '头条版本' }
async function recheckApproval(): Promise<void> { if (!projectId.value) return; const actor = approvalActor.value.trim(); if (!actor) { detailError.value = '请填写真实审批人或角色。'; return } try { await approvalsStore.recheck(projectId.value, actor) } catch (e) { detailError.value = unwrapError(e) } }
async function decideApproval(check: ApprovalCheck, approved: boolean): Promise<void> { if (!projectId.value) return; const actor = approvalActor.value.trim(); if (!actor) { detailError.value = '请填写真实审批人或角色。'; return } try { await approvalsStore.decide(projectId.value, check.id, approved, actor, approvalNotes.value[check.id]?.trim() || undefined); approvalNotes.value[check.id] = '' } catch (e) { detailError.value = unwrapError(e) } }
async function exportPackage(): Promise<void> { if (!projectId.value) return; exporting.value = true; try { exportResult.value = await approvalsStore.exportPackage(projectId.value) } catch (e) { detailError.value = unwrapError(e) } finally { exporting.value = false } }

function updateStatusForKind(): void {
  claimForm.value.status = claimForm.value.kind === 'open_question' ? 'open' : 'unverified'
}

async function saveSource(): Promise<void> {
  if (!projectId.value) return
  sourceSaving.value = true
  try {
    await researchStore.addSource(projectId.value, sourceForm.value)
    await refreshApprovalStatus()
    sourceForm.value = { title: '', reference: '', summary: '' }
  } catch (e) { detailError.value = unwrapError(e) } finally { sourceSaving.value = false }
}

async function saveClaim(): Promise<void> {
  if (!projectId.value) return
  claimSaving.value = true
  try {
    await researchStore.addClaim(projectId.value, {
      text: claimForm.value.text, kind: claimForm.value.kind, status: claimForm.value.status,
      source_ids: claimForm.value.source_ids, limitation: claimForm.value.limitation || null,
      counterpoint: claimForm.value.counterpoint || null,
    })
    await refreshApprovalStatus()
    claimForm.value = { text: '', kind: 'fact', status: 'unverified', source_ids: [], limitation: '', counterpoint: '' }
  } catch (e) { detailError.value = unwrapError(e) } finally { claimSaving.value = false }
}

onMounted(loadPage)
watch(projectId, loadPage)
</script>

<template>
  <section class="projects-page">
    <template v-if="projectId">
      <a-button type="link" class="back" @click="router.push('/projects')"><ArrowLeftOutlined /> 全部项目</a-button>
      <a-alert v-if="detailError" type="error" :message="detailError" show-icon />
      <article v-else-if="project" class="project-workspace">
        <header>
          <p class="eyebrow">主题项目</p>
          <h1>{{ project.title }}</h1>
          <p class="idea">{{ project.idea }}</p>
        </header>
        <div class="project-grid">
          <a-card title="创作意图" :bordered="false"><dl><dt>写给谁</dt><dd>{{ project.audience }}</dd><dt>这次要完成什么</dt><dd>{{ project.goal }}</dd><dt>声音</dt><dd>{{ project.voice }}</dd></dl></a-card>
          <a-card title="参考资料" :bordered="false" class="project-materials">
            <p v-if="!materials.length" class="muted">这篇文章还没有附加资料。</p>
            <article v-for="item in materials" :key="item.id" class="project-material-row">
              <div><strong>{{ item.original_name || item.source }}</strong><small>{{ item.analysis?.status === 'used' ? `已使用 · ${item.analysis.segments.length} 个可引用段落` : item.analysis?.status === 'not_used' ? `未使用：${item.analysis.error}` : '尚未读取' }}</small></div>
              <a-button size="small" :loading="parsingMaterial === item.id" @click="parseMaterial(item)">{{ item.analysis ? '重新读取' : '读取资料' }}</a-button>
              <p v-if="item.analysis?.status === 'used' && item.analysis.segments[0]" class="material-citation">{{ item.analysis.segments[0].citation }} · {{ item.analysis.segments[0].text }}</p>
            </article>
            <p class="muted">只会使用已读取的来源；坏链接会标为未使用，不影响继续起稿。</p>
          </a-card>
        </div>
        <nav class="workflow-cockpit" aria-label="创作流程">
          <div><p class="eyebrow">当前路径</p><h2>{{ nextStep.done ? '内容包已就绪' : `下一步：${nextStep.label}` }}</h2><p>一次只处理一个环节；已完成的步骤仍可回看，任何上游修改都会触发重新检查。</p></div>
          <div class="workflow-steps"><button v-for="step in workflowSteps" :key="step.key" :class="{ active: activeWorkbench === step.key, done: step.done }" @click="activeWorkbench = step.key"><span>{{ step.done ? '✓' : '○' }}</span>{{ step.label }}</button></div>
        </nav>
        <section v-show="activeWorkbench === 'research'" class="research-board">
          <header class="section-heading"><div><p class="eyebrow">研究板</p><h2>先把依据、判断和未知写清楚。</h2><p>来源不会自动核查。标记为“已核查”前，请自行确认原始材料。</p></div></header>
          <a-alert v-if="researchError" type="error" :message="researchError" show-icon class="notice" />
          <a-spin :spinning="researchLoading">
            <div class="research-alerts" v-if="unverifiedFacts.length || openQuestions.length">
              <a-alert v-if="unverifiedFacts.length" type="warning" show-icon :message="`${unverifiedFacts.length} 条事实尚未核查或缺少来源`" />
              <a-alert v-if="openQuestions.length" type="info" show-icon :message="`${openQuestions.length} 个问题仍待解决`" />
            </div>
            <div class="research-grid">
              <a-card title="来源" :bordered="false">
                <div v-if="board?.sources.length" class="record-list source-list"><article v-for="source in board.sources" :key="source.id"><a :href="source.reference" target="_blank" rel="noreferrer">{{ source.title }}</a><p>{{ source.summary }}</p><small>{{ source.reference }}</small></article></div>
                <a-empty v-else description="还没有来源" :image-style="{ height: '40px' }" />
                <a-form layout="vertical" class="research-form" @finish="saveSource"><a-form-item label="标题" required><a-input v-model:value="sourceForm.title" /></a-form-item><a-form-item label="URL 或本地引用" required><a-input v-model:value="sourceForm.reference" placeholder="https://… 或 notes/interview.md" /></a-form-item><a-form-item label="摘要" required><a-textarea v-model:value="sourceForm.summary" :rows="2" /></a-form-item><a-button html-type="button" :loading="sourceSaving" @click="saveSource">添加来源</a-button></a-form>
              </a-card>
              <a-card title="声明" :bordered="false">
                <div v-if="board?.claims.length" class="record-list claim-list"><article v-for="claim in board.claims" :key="claim.id" :class="{ unresolved: claim.kind === 'open_question' && claim.status === 'open', unverified: claim.kind === 'fact' && (claim.status === 'unverified' || !claim.source_ids.length) }"><div class="claim-meta"><a-tag :color="claim.kind === 'fact' ? 'blue' : claim.kind === 'judgment' ? 'gold' : 'purple'">{{ claim.kind === 'fact' ? '事实' : claim.kind === 'judgment' ? '判断' : '待确认' }}</a-tag><a-tag>{{ claim.status === 'verified' ? '已核查' : claim.status === 'resolved' ? '已解决' : claim.status === 'open' ? '待解决' : '待核查' }}</a-tag></div><p>{{ claim.text }}</p><small v-if="claim.source_ids.length">关联 {{ claim.source_ids.length }} 个来源</small><small v-else>未关联来源</small><p v-if="claim.limitation" class="caveat">限制：{{ claim.limitation }}</p><p v-if="claim.counterpoint" class="caveat">反方：{{ claim.counterpoint }}</p></article></div>
                <a-empty v-else description="还没有声明" :image-style="{ height: '40px' }" />
                <a-form layout="vertical" class="research-form" @finish="saveClaim"><a-form-item label="声明" required><a-textarea v-model:value="claimForm.text" :rows="2" /></a-form-item><div class="form-pair"><a-form-item label="类型" required><a-select v-model:value="claimForm.kind" @change="updateStatusForKind"><a-select-option value="fact">事实</a-select-option><a-select-option value="judgment">个人判断</a-select-option><a-select-option value="open_question">待确认问题</a-select-option></a-select></a-form-item><a-form-item label="状态" required><a-select v-model:value="claimForm.status"><a-select-option v-for="option in claimStatusOptions" :key="option.value" :value="option.value">{{ option.label }}</a-select-option></a-select></a-form-item></div><a-form-item label="关联来源"><a-select v-model:value="claimForm.source_ids" mode="multiple" placeholder="可留空，但事实会显示待核查"><a-select-option v-for="source in board?.sources ?? []" :key="source.id" :value="source.id">{{ source.title }}</a-select-option></a-select></a-form-item><a-form-item label="限制"><a-textarea v-model:value="claimForm.limitation" :rows="1" placeholder="可选" /></a-form-item><a-form-item label="反方观点"><a-textarea v-model:value="claimForm.counterpoint" :rows="1" placeholder="可选" /></a-form-item><a-button html-type="button" :loading="claimSaving" @click="saveClaim">添加声明</a-button></a-form>
              </a-card>
            </div>
          </a-spin>
        </section>
        <section v-show="activeWorkbench === 'master'" class="master-workbench">
          <header class="section-heading"><div><p class="eyebrow">主稿</p><h2>在这里写作，AI 只提出可审阅的修改。</h2><p>保存会创建新版本。AI 只有在你点击后才会生成建议，接受前不会改动正文。</p></div><a-tag v-if="master" color="blue">版本 {{ master.version }}</a-tag></header>
          <a-alert v-if="masterError" type="error" :message="masterError" show-icon class="notice" />
          <div class="draft-actions">
            <div><strong>从研究板生成第一稿</strong><p class="muted">AI 会区分已核查事实、个人判断和限制；结果先进入审阅区，不会自动覆盖主稿。</p></div>
            <a-button type="primary" :loading="draftGenerating" :disabled="!researchReady" @click="proposeDraft">AI 提出主稿初稿</a-button>
          </div>
          <a-card v-if="draftProposal" title="待审阅的 AI 初稿" :bordered="false" class="draft-proposal"><h3>{{ draftProposal.title }}</h3><p class="proposal-copy">{{ draftProposal.body }}</p><div class="proposal-actions"><a-button type="primary" @click="useDraftProposal">放入编辑器继续修改</a-button><a-button @click="draftProposal = null">丢弃</a-button></div></a-card>
          <p class="master-count">当前编辑器 {{ masterForm.body.trim().length }} 字；进入审批前至少需要 800 字。</p>
          <a-spin :spinning="masterLoading"><div class="master-grid"><a-card title="主稿编辑器" :bordered="false"><a-form layout="vertical"><a-form-item label="标题" required><a-input v-model:value="masterForm.title" placeholder="给主稿一个清晰标题" /></a-form-item><a-form-item label="正文" required><a-textarea v-model:value="masterForm.body" :rows="16" placeholder="从空白开始，或把已有的想法写下来。" @mouseup="captureSelection" /></a-form-item><p v-if="selectedText" class="selection-note">已选中 {{ selectedText.length }} 个字，建议只会替换这一段。</p><a-button type="primary" :loading="masterSaving" @click="saveMaster">保存为新版本</a-button></a-form></a-card><a-card title="AI 建议" :bordered="false"><p class="muted">{{ selectedText ? '建议将基于当前选区；未选文字时会针对全文。' : '先选中一段文字，或直接对全文提出建议。' }}</p><div class="suggestion-actions"><a-button :disabled="!master" :loading="suggestionSaving" @click="requestSuggestion('clarify')">改清楚</a-button><a-button :disabled="!master" :loading="suggestionSaving" @click="requestSuggestion('shorten')">压缩</a-button><a-button :disabled="!master" :loading="suggestionSaving" @click="requestSuggestion('change_voice')">换口吻</a-button><a-button :disabled="!master" :loading="suggestionSaving" @click="requestSuggestion('add_counterpoint')">补反方观点</a-button></div><div v-if="suggestions.length" class="proposal-list"><article v-for="suggestion in suggestions.slice().reverse()" :key="suggestion.id"><div class="proposal-meta"><a-tag>{{ suggestion.action }}</a-tag><span>{{ suggestion.status === 'pending' ? '待决定' : suggestion.status === 'accepted' ? '已接受' : '已拒绝' }}</span></div><p class="proposal-copy">{{ suggestion.proposed_body }}</p><div v-if="suggestion.status === 'pending'" class="proposal-actions"><a-button type="primary" size="small" @click="acceptSuggestion(suggestion)">接受为新版本</a-button><a-button size="small" @click="rejectSuggestion(suggestion)">拒绝</a-button></div></article></div><a-empty v-else description="还没有 AI 建议" :image-style="{ height: '40px' }" /></a-card></div><a-card v-if="master" title="版本与恢复" :bordered="false" class="version-card"><p class="muted">恢复并不会覆盖历史，而是用所选版本创建新的当前版本。</p><div class="version-list"><article v-for="version in [...master.history, { version: master.version, title: master.title, body: master.body, saved_at: master.updated_at, reason: 'current' }]" :key="version.version"><div><strong>版本 {{ version.version }}</strong><span>{{ version.reason === 'current' ? '当前版本' : version.reason }}</span><p>{{ version.body.slice(0, 100) }}{{ version.body.length > 100 ? '…' : '' }}</p></div><a-button v-if="version.version !== master.version" size="small" @click="restoreVersion(version.version)">恢复为新版本</a-button></article></div></a-card></a-spin>
        </section>
        <section v-show="activeWorkbench === 'visuals'" class="visual-workbench">
          <header class="section-heading"><div><p class="eyebrow">视觉计划</p><h2>先定义意图，再生成候选。</h2><p>候选不会写入主稿或平台版本。成本显示为请求前预估，实际账单以 OpenAI 用量账单为准。</p></div></header>
          <a-alert v-if="visualsError" type="error" :message="visualsError" show-icon class="notice" />
          <a-alert v-if="visualProvider && !visualProvider.available" type="warning" show-icon class="notice" :message="visualProvider.reason || 'GPT Image 2 暂不可用'" description="可以继续完成视觉计划，并为每个槽位导入本地 PNG；导入会保留提示词、版本和选择记录。" />
          <div class="visual-bootstrap"><a-button v-if="!visualSlots.length" type="primary" @click="setupStandardVisuals">建立 1 张封面 + 2 张插图</a-button><p v-else class="muted">已规划 {{ visualSlots.length }} 个槽位；保存计划后，可以生成或导入候选。</p></div>
          <div v-if="visualSlots.length" class="local-imports"><strong>本地 PNG 兜底</strong><label v-for="slot in visualSlots" :key="`import-${slot.id}`" class="import-button"><span>{{ importingSlot === slot.id ? '正在导入…' : `为「${slot.purpose}」导入 PNG` }}</span><input type="file" accept="image/png" :disabled="importingSlot !== null" @change="importVisual($event, slot)" /></label></div>
          <div v-if="visualPlan?.assets.some(asset => visualAssetUrl(asset))" class="asset-gallery"><figure v-for="asset in visualPlan.assets.filter(item => visualAssetUrl(item))" :key="`preview-${asset.id}`"><img :src="visualAssetUrl(asset) || ''" :alt="asset.prompt" /><figcaption>{{ visualSlots.find(slot => slot.id === asset.slot_id)?.purpose }} · {{ asset.status === 'selected' ? '已选择' : '候选' }}</figcaption></figure></div>
          <a-spin :spinning="visualsLoading"><a-card :bordered="false" class="visual-card"><a-form layout="vertical"><a-form-item label="视觉圣经"><a-textarea v-model:value="visualBible" :rows="3" placeholder="例如：风格: 克制的编辑插画\n色彩: 暖白纸张与墨蓝" /></a-form-item><div class="visual-slot-list"><article v-for="(slot, index) in visualSlots" :key="slot.id" class="visual-slot"><div class="slot-heading"><strong>{{ slot.purpose || `槽位 ${index + 1}` }}</strong><a-button type="link" danger size="small" @click="visualSlots.splice(index, 1)">移除</a-button></div><div class="form-pair"><a-form-item label="用途"><a-input v-model:value="slot.purpose" placeholder="封面 / 正文插图" /></a-form-item><a-form-item label="比例"><a-select v-model:value="slot.aspect_ratio"><a-select-option value="16:9">16:9 横图</a-select-option><a-select-option value="1:1">1:1 方图</a-select-option><a-select-option value="9:16">9:16 竖图</a-select-option><a-select-option value="4:3">4:3</a-select-option><a-select-option value="3:4">3:4</a-select-option></a-select></a-form-item></div><a-form-item label="对应段落（可选）"><a-input v-model:value="slot.paragraph_anchor" placeholder="例如：开头的核心问题" /></a-form-item><a-form-item label="画面方向"><a-textarea v-model:value="slot.direction" :rows="2" placeholder="这张图要帮助读者理解什么？" /></a-form-item><a-form-item label="生成或编辑提示词"><a-textarea v-model:value="visualPrompts[slot.id]" :rows="2" placeholder="显式点击后才会调用 GPT Image 2" /></a-form-item><div class="visual-actions"><a-button :loading="visualGenerating === slot.id" :disabled="!visualProvider?.available || !visualPrompts[slot.id]?.trim()" @click="generateVisual(slot)">生成候选</a-button></div><div v-if="visualPlan?.assets.filter(asset => asset.slot_id === slot.id).length" class="asset-list"><article v-for="asset in visualPlan.assets.filter(item => item.slot_id === slot.id).slice().reverse()" :key="asset.id" :class="['visual-asset', asset.status]"><div><a-tag :color="asset.status === 'selected' ? 'green' : asset.status === 'failed' ? 'red' : 'blue'">{{ asset.status === 'selected' ? '已选择' : asset.status === 'failed' ? '失败' : '候选' }}</a-tag><span>v{{ asset.version }} · {{ asset.model }} · 预估 ${{ asset.cost_usd.toFixed(2) }}</span></div><p>{{ asset.prompt }}</p><p v-if="asset.failure" class="failure">{{ asset.failure }}</p><div v-if="asset.status !== 'failed'" class="asset-actions"><a-button v-if="asset.status !== 'selected'" size="small" @click="selectVisual(asset.id)">选择</a-button><a-button size="small" :loading="visualGenerating === slot.id" :disabled="!visualProvider?.available || !visualPrompts[slot.id]?.trim()" @click="generateVisual(slot, asset.id)">基于此编辑</a-button></div></article></div></article></div><div class="visual-plan-actions"><a-button @click="addVisualSlot">添加插图槽位</a-button><a-button type="primary" :loading="visualSaving" @click="saveVisualPlan">保存视觉计划</a-button></div></a-form></a-card></a-spin>
        </section>
        <section v-show="activeWorkbench === 'variants'" class="variants-workbench">
          <header class="section-heading"><div><p class="eyebrow">平台版本</p><h2>共享主张，各自完成排版。</h2><p>创建后，微信和头条各自独立编辑。主稿更新只会提示你，不会自动覆盖人工修改。</p></div></header>
          <a-alert v-if="variantsError" type="error" :message="variantsError" show-icon class="notice" />
          <div class="variant-adapt"><div v-for="platform in ['wechat_mp', 'toutiao']" :key="`adapt-${platform}`"><strong>{{ platform === 'wechat_mp' ? '微信公众号' : '今日头条' }}</strong><a-button type="primary" :loading="variantGenerating === platform" :disabled="variants.some(item => item.platform === platform) || !master" @click="createVariant(platform as PlatformVariant['platform'], true)">AI 适配平台初稿</a-button></div><p class="muted">AI 结果仍是独立草稿，必须人工编辑并锁定；如不可用，可使用下方“复制主稿”兜底。</p></div>
          <a-spin :spinning="variantsLoading"><div class="variant-create"><a-button v-for="platform in ['wechat_mp', 'toutiao']" :key="platform" :disabled="variants.some(item => item.platform === platform) || !master" @click="createVariant(platform as PlatformVariant['platform'])">创建{{ platform === 'wechat_mp' ? '微信公众号' : '今日头条' }}初稿</a-button></div><p v-if="!master" class="muted">先保存主稿，才能创建平台版本。</p><div class="variant-list"><a-card v-for="item in variants" :key="item.platform" :title="platformName(item.platform)" :bordered="false"><template #extra><a-tag v-if="item.locked" color="gold">已锁定</a-tag><a-tag v-if="item.upstream_updated || (master && item.source_master_version !== master.version)" color="orange">主稿有更新</a-tag></template><a-alert v-if="item.upstream_updated || (master && item.source_master_version !== master.version)" type="warning" show-icon message="主稿有更新。先解锁并人工合并必要修改，再点击“确认已合并当前主稿”；系统不会覆盖平台稿。" class="notice"/><a-form layout="vertical"><a-form-item label="标题"><a-input v-model:value="variantForm(item.platform).title" :disabled="item.locked" /></a-form-item><a-form-item label="摘要"><a-textarea v-model:value="variantForm(item.platform).summary" :rows="2" :disabled="item.locked" /></a-form-item><a-form-item label="正文"><a-textarea v-model:value="variantForm(item.platform).body" :rows="8" :disabled="item.locked" /></a-form-item><div class="variant-actions"><a-button type="primary" :disabled="item.locked" @click="saveVariant(item)">保存独立版本</a-button><a-button @click="toggleLock(item)">{{ item.locked ? '解锁编辑' : '锁定版本' }}</a-button><a-button @click="checkVariantUpstream(item)">检查主稿更新</a-button><a-button v-if="master && item.source_master_version !== master.version" :disabled="item.locked" @click="acknowledgeVariantMaster(item)">确认已合并当前主稿 v{{ master.version }}</a-button><a-button @click="previewVariant(item)">打开只读预览</a-button></div><p class="muted">源主稿 v{{ item.source_master_version }} · 平台版本 v{{ item.version }} · {{ item.manually_modified ? '已人工修改' : '尚未人工修改' }}</p><div v-if="item.history.length" class="variant-history"><span>历史版本：</span><a-button v-for="version in item.history" :key="version.version" size="small" :disabled="item.locked" @click="restoreVariant(item, version.version)">恢复 v{{ version.version }}</a-button></div></a-form></a-card></div></a-spin>
        </section>
        <section v-show="activeWorkbench === 'approval'" class="approval-workbench">
          <header class="section-heading"><div><p class="eyebrow">内容包审批</p><h2>逐项确认，再进入安全交付。</h2><p>批准只记录你的人工判断，不会发布、创建平台草稿或调用任何发布器。</p></div><a-tag v-if="approvalStatus?.complete" color="green">已完成审批</a-tag></header>
          <a-alert v-if="approvalsError" type="error" :message="approvalsError" show-icon class="notice" />
          <div class="approval-actor"><label for="approval-actor">真实审批人或角色</label><a-input id="approval-actor" v-model:value="approvalActor" placeholder="例如：张三 / Codex 自测（受用户委托）" /></div>
          <a-spin :spinning="approvalsLoading"><a-card :bordered="false" class="approval-card"><a-alert v-if="approvalStatus?.blockers.length" type="warning" show-icon :message="`尚不可审批：${approvalStatus?.blockers.join('；')}`" class="notice"/><a-alert v-else-if="approvalStatus?.stale" type="warning" show-icon message="上游内容已改变，请重新检查。历史批准不会被静默沿用；所有批准与撤回动作已暂停。" class="notice"/><div class="approval-actions"><a-button type="primary" @click="recheckApproval">重新检查内容包</a-button></div><div v-if="approvalStatus?.approval.checks.length" class="approval-list"><article v-for="check in approvalStatus.approval.checks" :key="check.id"><div><strong>{{ approvalLabel[check.id] }}</strong><p>{{ check.status === 'approved' ? `已由 ${check.approved_by} 批准` : '待人工检查' }}</p><small v-if="check.note">当前备注：{{ check.note }}</small></div><div class="approval-decision"><a-input v-model:value="approvalNotes[check.id]" :disabled="!approvalStatus.ready || approvalStatus.stale" placeholder="可选审批备注" size="small"/><a-button v-if="check.status !== 'approved'" type="primary" size="small" :disabled="!approvalStatus.ready || approvalStatus.stale" @click="decideApproval(check, true)">批准</a-button><a-button v-else size="small" :disabled="!approvalStatus.ready || approvalStatus.stale" @click="decideApproval(check, false)">撤回批准</a-button></div></article></div><a-empty v-else description="先重新检查，生成当前内容包的审批清单。" :image-style="{ height: '40px' }"/><p v-if="approvalStatus?.complete" class="approval-complete">所有项目已批准。下一步仅可进入草稿箱或安全导出，仍不等于真实发布。</p><div v-if="approvalStatus?.approval.history.length" class="approval-history"><strong>审批历史</strong><p v-for="event in approvalStatus.approval.history.slice().reverse().slice(0, 8)" :key="`${event.at}-${event.action}-${event.check_id}`">{{ event.at }} · {{ event.actor }} · {{ event.action }}{{ event.check_id ? ` (${approvalLabel[event.check_id]})` : '' }}{{ event.note ? ` · ${event.note}` : '' }}</p></div></a-card></a-spin>
          <div v-if="approvalStatus?.complete" class="export-panel"><div><strong>生成可交付内容包</strong><p>只在本地导出微信稿、头条稿、清单和已选图片；不会创建发布记录或调用平台。</p></div><a-button type="primary" :loading="exporting" @click="exportPackage">导出 ZIP</a-button><a v-if="exportResult" :href="exportResult.url" target="_blank" rel="noreferrer">下载 {{ exportResult.file_name }}</a></div>
        </section>
      </article>
    </template>

    <template v-else>
      <header class="list-header"><div><p class="eyebrow">项目</p><h1>每一个主题，都有一张自己的工作台。</h1><p>项目把想法、资料、主稿、视觉与平台版本放在同一条创作路径上。</p></div><a-button type="primary" @click="router.push('/projects/new')">新建项目</a-button></header>
      <a-alert v-if="error" type="error" :message="error" show-icon class="notice" />
      <a-spin :spinning="loading">
        <div v-if="items.length" class="project-list"><button v-for="item in items" :key="item.id" class="project-row" @click="router.push(`/projects/${item.id}`)"><div><h2>{{ item.title }}</h2><p>{{ item.idea }}</p><span>{{ item.audience }} · {{ item.goal }}</span></div><div class="row-meta"><time>{{ formatDateTime(item.updated_at) }}</time><ArrowRightOutlined /></div></button></div>
        <a-empty v-else-if="!loading" description="还没有项目。下一步可以从一个真实主题开始。"><template #image><FolderOpenOutlined class="empty-icon" /></template></a-empty>
      </a-spin>
      <p class="count" v-if="total">共 {{ total }} 个项目</p>
    </template>
  </section>
</template>

<style scoped>
.projects-page { max-width: 1020px; padding: 24px 0 56px; }
.eyebrow { margin: 0 0 8px; color: #7a6650; font-size: 12px; font-weight: 700; letter-spacing: .09em; text-transform: uppercase; }
h1, h2 { color: #292522; font-family: Georgia, 'Songti SC', serif; } h1 { margin: 0 0 12px; font-size: clamp(30px, 4vw, 44px); line-height: 1.2; } h2 { margin: 0 0 8px; font-size: 22px; }
.list-header { display: flex; justify-content: space-between; align-items: flex-end; gap: 20px; margin-bottom: 28px; }.list-header > div { max-width: 720px; }.list-header p, .idea, .project-row p, .project-row span, .project-workspace p { color: #706b65; line-height: 1.7; }.notice { margin-bottom: 16px; }
.project-list { border-top: 1px solid #ded7cd; }.project-row { width: 100%; display: flex; justify-content: space-between; gap: 24px; padding: 22px 4px; text-align: left; border: 0; border-bottom: 1px solid #ded7cd; background: transparent; cursor: pointer; }.project-row:hover h2 { color: #886d4b; }.project-row p { max-width: 700px; margin: 0 0 6px; }.project-row span, .row-meta { color: #948d84; font-size: 13px; }.row-meta { display: flex; align-items: center; gap: 16px; white-space: nowrap; }.empty-icon { color: #b39b79; font-size: 44px; }.count { color: #948d84; font-size: 13px; }.back { margin-bottom: 12px; padding-left: 0; }.project-workspace > header { max-width: 760px; margin-bottom: 28px; }.idea { font-size: 18px; }.project-grid, .research-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }.project-grid :deep(.ant-card), .research-grid :deep(.ant-card) { background: #fffdf8; border: 1px solid #e8e1d5; box-shadow: none; }.project-grid dd { margin: 4px 0 16px; color: #4e4943; }.project-grid dt { color: #948d84; font-size: 12px; }.muted { color: #948d84 !important; }.project-material-row { margin: 10px 0; padding: 9px 0; border-top: 1px solid #eee8df; }.project-material-row:first-of-type { border-top: 0; }.project-material-row > div { display: flex; justify-content: space-between; gap: 10px; }.project-material-row strong,.project-material-row small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.project-material-row small { color: #837b70; font-size: 12px; }.project-material-row .material-citation { margin: 7px 0 0; color: #635b51; font-size: 12px; line-height: 1.45; }.research-board { margin-top: 34px; max-width: 1000px; }.section-heading { margin-bottom: 18px; }.section-heading p { max-width: 680px; }.research-alerts { display: grid; gap: 8px; margin-bottom: 16px; }.record-list { display: grid; gap: 10px; margin-bottom: 20px; }.record-list article { padding: 12px; border-left: 3px solid #d8c9b5; background: #faf7f1; }.record-list p { margin: 6px 0; color: #5e5851; }.record-list small { color: #897f75; word-break: break-word; }.claim-meta { display: flex; gap: 6px; }.claim-list .unverified { border-left-color: #d89614; }.claim-list .unresolved { border-left-color: #7f59b0; }.caveat { color: #7a5d3d !important; }.research-form { padding-top: 12px; border-top: 1px solid #e8e1d5; }.form-pair { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.workflow-cockpit { position: sticky; top: 12px; z-index: 4; display: grid; grid-template-columns: minmax(240px, .7fr) 1.3fr; gap: 20px; margin: 26px 0 6px; padding: 18px; border: 1px solid #ded7cd; border-radius: 12px; background: rgba(255, 253, 248, .96); box-shadow: 0 10px 30px rgba(75, 60, 40, .08); backdrop-filter: blur(8px); }.workflow-cockpit h2 { font-size: 18px; }.workflow-cockpit p { margin: 0; font-size: 13px; }.workflow-steps { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); align-items: stretch; gap: 6px; }.workflow-steps button { display: grid; place-content: center; gap: 3px; min-height: 62px; padding: 7px; border: 1px solid #ded7cd; border-radius: 8px; color: #706b65; background: #fff; cursor: pointer; }.workflow-steps button.active { color: #60482d; border-color: #a6845b; background: #f5eee3; }.workflow-steps button.done { color: #39704b; }.workflow-steps span { font-weight: 700; }
.master-workbench { margin-top: 34px; }.master-grid { display: grid; grid-template-columns: 1.25fr .75fr; gap: 16px; }.master-grid :deep(.ant-card), .version-card { background: #fffdf8; border: 1px solid #e8e1d5; box-shadow: none; }.suggestion-actions, .proposal-actions { display: flex; flex-wrap: wrap; gap: 8px; }.proposal-list { display: grid; gap: 10px; margin-top: 16px; }.proposal-list article { padding: 12px; border-left: 3px solid #d8c9b5; background: #faf7f1; }.proposal-meta { display: flex; justify-content: space-between; gap: 8px; color: #948d84; font-size: 12px; }.proposal-copy { max-height: 160px; overflow: auto; white-space: pre-wrap; color: #4e4943; }.version-card { margin-top: 16px; }.version-list { display: grid; gap: 8px; }.version-list article { display: flex; justify-content: space-between; gap: 12px; padding: 10px 0; border-top: 1px solid #e8e1d5; }.version-list span { margin-left: 8px; color: #948d84; font-size: 12px; }.version-list p { margin: 4px 0 0; color: #706b65; }.selection-note { color: #7a6650; font-size: 13px; }
.draft-actions, .export-panel, .variant-adapt, .local-imports { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; margin: 0 0 16px; padding: 14px; border: 1px solid #e8e1d5; border-radius: 8px; background: #fffdf8; }.draft-actions p, .export-panel p { margin: 4px 0 0; }.draft-proposal { margin-bottom: 16px; border-color: #c7b497; background: #fbf6ed; }.draft-proposal .proposal-copy { max-height: 360px; }.master-count { color: #7a6650 !important; font-size: 13px; }.variant-adapt > div { display: flex; align-items: center; gap: 10px; }.variant-adapt > p { flex-basis: 100%; margin: 0; }.local-imports { justify-content: flex-start; }.import-button { display: inline-flex; padding: 6px 11px; border: 1px dashed #a6845b; border-radius: 6px; color: #60482d; cursor: pointer; background: #fff; }.import-button input { position: absolute; width: 1px; height: 1px; opacity: 0; }.visual-bootstrap { margin-bottom: 12px; }
.asset-gallery { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; margin-bottom: 16px; }.asset-gallery figure { margin: 0; padding: 8px; border: 1px solid #e8e1d5; border-radius: 8px; background: #fff; }.asset-gallery img { display: block; width: 100%; aspect-ratio: 16 / 9; object-fit: cover; border-radius: 5px; }.asset-gallery figcaption { padding-top: 7px; color: #706b65; font-size: 12px; }
.visual-workbench { margin-top: 34px; }.visual-card { background: #fffdf8; border: 1px solid #e8e1d5; box-shadow: none; }.visual-slot-list { display: grid; gap: 14px; }.visual-slot { padding: 14px; border: 1px solid #e8e1d5; background: #faf7f1; }.slot-heading { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }.visual-actions, .visual-plan-actions, .asset-actions { display: flex; flex-wrap: wrap; gap: 8px; }.visual-plan-actions { margin-top: 16px; }.asset-list { display: grid; gap: 8px; margin-top: 12px; }.visual-asset { padding: 10px; border-left: 3px solid #9db7cc; background: #fffdf8; }.visual-asset.selected { border-left-color: #52a36b; }.visual-asset.failed { border-left-color: #cf5d50; }.visual-asset span { margin-left: 8px; color: #897f75; font-size: 12px; }.visual-asset p { margin: 7px 0; color: #5e5851; white-space: pre-wrap; }.failure { color: #b44336 !important; }
.variants-workbench { margin-top: 34px; }.variant-create, .variant-actions { display: flex; flex-wrap: wrap; gap: 8px; }.variant-create { margin-bottom: 12px; }.variant-list { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }.variant-list :deep(.ant-card) { background: #fffdf8; border: 1px solid #e8e1d5; box-shadow: none; }
.approval-workbench { margin-top: 34px; }.approval-actor { display: grid; grid-template-columns: 150px minmax(220px, 420px); align-items: center; gap: 10px; margin-bottom: 14px; color: #706b65; font-size: 13px; }.approval-card { background: #fffdf8; border: 1px solid #e8e1d5; box-shadow: none; }.approval-actions { margin-bottom: 14px; }.approval-list { display: grid; gap: 8px; }.approval-list article { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 12px 0; border-top: 1px solid #e8e1d5; }.approval-list p, .approval-list small { margin: 4px 0 0; color: #706b65; }.approval-decision { display: grid; grid-template-columns: minmax(130px, 220px) auto; gap: 8px; align-items: center; }.approval-complete { margin-top: 16px; color: #39704b !important; }.approval-history { margin-top: 18px; padding-top: 14px; border-top: 1px solid #e8e1d5; }.approval-history p { margin: 5px 0; color: #897f75; font-size: 12px; }
@media (max-width: 760px) { .list-header, .project-row { align-items: flex-start; flex-direction: column; }.project-grid, .research-grid, .form-pair, .variant-list, .workflow-cockpit { grid-template-columns: 1fr; }.workflow-cockpit { position: static; }.workflow-steps { grid-template-columns: repeat(5, minmax(110px, 1fr)); overflow-x: auto; }.row-meta { width: 100%; justify-content: space-between; } }
</style>
