<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import { ArrowLeftOutlined, ArrowRightOutlined, FolderOpenOutlined } from '@ant-design/icons-vue'
import { useProjectsStore, useResearchStore, useMasterStore, useVisualsStore, type ProjectItem, type ResearchClaim, type MasterSuggestion, type VisualSlot } from '../stores'
import { unwrapError } from '../api/client'
import { formatDateTime } from '../utils/format'

const route = useRoute()
const router = useRouter()
const store = useProjectsStore()
const researchStore = useResearchStore()
const masterStore = useMasterStore()
const visualsStore = useVisualsStore()
const { items, total, loading, error } = storeToRefs(store)
const { board, loading: researchLoading, error: researchError } = storeToRefs(researchStore)
const { master, suggestions, loading: masterLoading, error: masterError } = storeToRefs(masterStore)
const { plan: visualPlan, loading: visualsLoading, error: visualsError } = storeToRefs(visualsStore)
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

const unverifiedFacts = computed(() => board.value?.claims.filter(item => item.kind === 'fact' && (item.status === 'unverified' || !item.source_ids.length)) ?? [])
const openQuestions = computed(() => board.value?.claims.filter(item => item.kind === 'open_question' && item.status === 'open') ?? [])
const claimStatusOptions = computed(() => claimForm.value.kind === 'open_question'
  ? [{ value: 'open', label: '待解决' }, { value: 'resolved', label: '已解决' }]
  : [{ value: 'unverified', label: '待核查' }, { value: 'verified', label: '已核查' }])

async function loadPage(): Promise<void> {
  detailError.value = null
  project.value = null
  if (!projectId.value) {
    await store.load()
    return
  }
  try {
    project.value = await store.getDetail(projectId.value)
    await researchStore.load(projectId.value)
    await masterStore.load(projectId.value)
    await visualsStore.load(projectId.value)
    if (master.value) masterForm.value = { title: master.value.title, body: master.value.body }
    visualBible.value = Object.entries(visualPlan.value?.bible ?? {}).map(([key, value]) => `${key}: ${value}`).join('\n')
    visualSlots.value = visualPlan.value?.slots.map(slot => ({ ...slot })) ?? []
  } catch (e) {
    detailError.value = unwrapError(e)
  }
}

async function saveMaster(): Promise<void> {
  if (!projectId.value) return
  masterSaving.value = true
  try { await masterStore.save(projectId.value, masterForm.value) } catch (e) { detailError.value = unwrapError(e) } finally { masterSaving.value = false }
}
function captureSelection(): void { selectedText.value = document.getSelection()?.toString().trim() ?? '' }
async function requestSuggestion(action: MasterSuggestion['action']): Promise<void> {
  if (!projectId.value || !master.value) return
  suggestionSaving.value = true
  try { await masterStore.request(projectId.value, { action, selection: selectedText.value || null }) } catch (e) { detailError.value = unwrapError(e) } finally { suggestionSaving.value = false }
}
async function acceptSuggestion(suggestion: MasterSuggestion): Promise<void> {
  if (!projectId.value) return
  try { const updated = await masterStore.accept(projectId.value, suggestion.id); masterForm.value = { title: updated.title, body: updated.body } } catch (e) { detailError.value = unwrapError(e) }
}
async function rejectSuggestion(suggestion: MasterSuggestion): Promise<void> {
  if (!projectId.value) return
  try { await masterStore.reject(projectId.value, suggestion.id) } catch (e) { detailError.value = unwrapError(e) }
}
async function restoreVersion(version: number): Promise<void> {
  if (!projectId.value) return
  try { const updated = await masterStore.restore(projectId.value, version); masterForm.value = { title: updated.title, body: updated.body } } catch (e) { detailError.value = unwrapError(e) }
}

function addVisualSlot(): void {
  visualSlots.value.push({ id: `vsl_${Date.now().toString(36)}`, purpose: '正文插图', paragraph_anchor: null, direction: '围绕主稿核心观点的编辑插画，保留必要留白。', aspect_ratio: '16:9' })
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
  try { await visualsStore.save(projectId.value, { bible, slots: visualSlots.value }) } catch (e) { detailError.value = unwrapError(e) } finally { visualSaving.value = false }
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
  try { await visualsStore.select(projectId.value, assetId, '适合本次主稿的视觉意图', 4) } catch (e) { detailError.value = unwrapError(e) }
}

function updateStatusForKind(): void {
  claimForm.value.status = claimForm.value.kind === 'open_question' ? 'open' : 'unverified'
}

async function saveSource(): Promise<void> {
  if (!projectId.value) return
  sourceSaving.value = true
  try {
    await researchStore.addSource(projectId.value, sourceForm.value)
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
          <a-card title="目前的材料" :bordered="false"><p>已关联 {{ project.content_ids.length }} 篇内容，{{ project.asset_paths.length }} 项资产。</p><p class="muted">来源、判断和待确认项均由你明确录入，不会自动抓取或改写。</p></a-card>
        </div>
        <section class="research-board">
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
        <section class="master-workbench">
          <header class="section-heading"><div><p class="eyebrow">主稿</p><h2>在这里写作，AI 只提出可审阅的修改。</h2><p>保存会创建新版本。AI 只有在你点击后才会生成建议，接受前不会改动正文。</p></div><a-tag v-if="master" color="blue">版本 {{ master.version }}</a-tag></header>
          <a-alert v-if="masterError" type="error" :message="masterError" show-icon class="notice" />
          <a-spin :spinning="masterLoading"><div class="master-grid"><a-card title="主稿编辑器" :bordered="false"><a-form layout="vertical"><a-form-item label="标题" required><a-input v-model:value="masterForm.title" placeholder="给主稿一个清晰标题" /></a-form-item><a-form-item label="正文" required><a-textarea v-model:value="masterForm.body" :rows="16" placeholder="从空白开始，或把已有的想法写下来。" @mouseup="captureSelection" /></a-form-item><p v-if="selectedText" class="selection-note">已选中 {{ selectedText.length }} 个字，建议只会替换这一段。</p><a-button type="primary" :loading="masterSaving" @click="saveMaster">保存为新版本</a-button></a-form></a-card><a-card title="AI 建议" :bordered="false"><p class="muted">{{ selectedText ? '建议将基于当前选区；未选文字时会针对全文。' : '先选中一段文字，或直接对全文提出建议。' }}</p><div class="suggestion-actions"><a-button :disabled="!master" :loading="suggestionSaving" @click="requestSuggestion('clarify')">改清楚</a-button><a-button :disabled="!master" :loading="suggestionSaving" @click="requestSuggestion('shorten')">压缩</a-button><a-button :disabled="!master" :loading="suggestionSaving" @click="requestSuggestion('change_voice')">换口吻</a-button><a-button :disabled="!master" :loading="suggestionSaving" @click="requestSuggestion('add_counterpoint')">补反方观点</a-button></div><div v-if="suggestions.length" class="proposal-list"><article v-for="suggestion in suggestions.slice().reverse()" :key="suggestion.id"><div class="proposal-meta"><a-tag>{{ suggestion.action }}</a-tag><span>{{ suggestion.status === 'pending' ? '待决定' : suggestion.status === 'accepted' ? '已接受' : '已拒绝' }}</span></div><p class="proposal-copy">{{ suggestion.proposed_body }}</p><div v-if="suggestion.status === 'pending'" class="proposal-actions"><a-button type="primary" size="small" @click="acceptSuggestion(suggestion)">接受为新版本</a-button><a-button size="small" @click="rejectSuggestion(suggestion)">拒绝</a-button></div></article></div><a-empty v-else description="还没有 AI 建议" :image-style="{ height: '40px' }" /></a-card></div><a-card v-if="master" title="版本与恢复" :bordered="false" class="version-card"><p class="muted">恢复并不会覆盖历史，而是用所选版本创建新的当前版本。</p><div class="version-list"><article v-for="version in [...master.history, { version: master.version, title: master.title, body: master.body, saved_at: master.updated_at, reason: 'current' }]" :key="version.version"><div><strong>版本 {{ version.version }}</strong><span>{{ version.reason === 'current' ? '当前版本' : version.reason }}</span><p>{{ version.body.slice(0, 100) }}{{ version.body.length > 100 ? '…' : '' }}</p></div><a-button v-if="version.version !== master.version" size="small" @click="restoreVersion(version.version)">恢复为新版本</a-button></article></div></a-card></a-spin>
        </section>
        <section class="visual-workbench">
          <header class="section-heading"><div><p class="eyebrow">视觉计划</p><h2>先定义意图，再生成候选。</h2><p>候选不会写入主稿或平台版本。成本显示为请求前预估，实际账单以 OpenAI 用量账单为准。</p></div></header>
          <a-alert v-if="visualsError" type="error" :message="visualsError" show-icon class="notice" />
          <a-spin :spinning="visualsLoading"><a-card :bordered="false" class="visual-card"><a-form layout="vertical"><a-form-item label="视觉圣经"><a-textarea v-model:value="visualBible" :rows="3" placeholder="例如：风格: 克制的编辑插画\n色彩: 暖白纸张与墨蓝" /></a-form-item><div class="visual-slot-list"><article v-for="(slot, index) in visualSlots" :key="slot.id" class="visual-slot"><div class="slot-heading"><strong>{{ slot.purpose || `槽位 ${index + 1}` }}</strong><a-button type="link" danger size="small" @click="visualSlots.splice(index, 1)">移除</a-button></div><div class="form-pair"><a-form-item label="用途"><a-input v-model:value="slot.purpose" placeholder="封面 / 正文插图" /></a-form-item><a-form-item label="比例"><a-select v-model:value="slot.aspect_ratio"><a-select-option value="16:9">16:9 横图</a-select-option><a-select-option value="1:1">1:1 方图</a-select-option><a-select-option value="9:16">9:16 竖图</a-select-option><a-select-option value="4:3">4:3</a-select-option><a-select-option value="3:4">3:4</a-select-option></a-select></a-form-item></div><a-form-item label="对应段落（可选）"><a-input v-model:value="slot.paragraph_anchor" placeholder="例如：开头的核心问题" /></a-form-item><a-form-item label="画面方向"><a-textarea v-model:value="slot.direction" :rows="2" placeholder="这张图要帮助读者理解什么？" /></a-form-item><a-form-item label="生成或编辑提示词"><a-textarea v-model:value="visualPrompts[slot.id]" :rows="2" placeholder="显式点击后才会调用 GPT Image 2" /></a-form-item><div class="visual-actions"><a-button :loading="visualGenerating === slot.id" :disabled="!visualPrompts[slot.id]?.trim()" @click="generateVisual(slot)">生成候选</a-button></div><div v-if="visualPlan?.assets.filter(asset => asset.slot_id === slot.id).length" class="asset-list"><article v-for="asset in visualPlan.assets.filter(item => item.slot_id === slot.id).slice().reverse()" :key="asset.id" :class="['visual-asset', asset.status]"><div><a-tag :color="asset.status === 'selected' ? 'green' : asset.status === 'failed' ? 'red' : 'blue'">{{ asset.status === 'selected' ? '已选择' : asset.status === 'failed' ? '失败' : '候选' }}</a-tag><span>v{{ asset.version }} · {{ asset.model }} · 预估 ${{ asset.cost_usd.toFixed(2) }}</span></div><p>{{ asset.prompt }}</p><p v-if="asset.failure" class="failure">{{ asset.failure }}</p><div v-if="asset.status !== 'failed'" class="asset-actions"><a-button v-if="asset.status !== 'selected'" size="small" @click="selectVisual(asset.id)">选择</a-button><a-button size="small" :loading="visualGenerating === slot.id" :disabled="!visualPrompts[slot.id]?.trim()" @click="generateVisual(slot, asset.id)">基于此编辑</a-button></div></article></div></article></div><div class="visual-plan-actions"><a-button @click="addVisualSlot">添加插图槽位</a-button><a-button type="primary" :loading="visualSaving" @click="saveVisualPlan">保存视觉计划</a-button></div></a-form></a-card></a-spin>
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
.project-list { border-top: 1px solid #ded7cd; }.project-row { width: 100%; display: flex; justify-content: space-between; gap: 24px; padding: 22px 4px; text-align: left; border: 0; border-bottom: 1px solid #ded7cd; background: transparent; cursor: pointer; }.project-row:hover h2 { color: #886d4b; }.project-row p { max-width: 700px; margin: 0 0 6px; }.project-row span, .row-meta { color: #948d84; font-size: 13px; }.row-meta { display: flex; align-items: center; gap: 16px; white-space: nowrap; }.empty-icon { color: #b39b79; font-size: 44px; }.count { color: #948d84; font-size: 13px; }.back { margin-bottom: 12px; padding-left: 0; }.project-workspace > header { max-width: 760px; margin-bottom: 28px; }.idea { font-size: 18px; }.project-grid, .research-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }.project-grid :deep(.ant-card), .research-grid :deep(.ant-card) { background: #fffdf8; border: 1px solid #e8e1d5; box-shadow: none; }.project-grid dd { margin: 4px 0 16px; color: #4e4943; }.project-grid dt { color: #948d84; font-size: 12px; }.muted { color: #948d84 !important; }.research-board { margin-top: 34px; max-width: 1000px; }.section-heading { margin-bottom: 18px; }.section-heading p { max-width: 680px; }.research-alerts { display: grid; gap: 8px; margin-bottom: 16px; }.record-list { display: grid; gap: 10px; margin-bottom: 20px; }.record-list article { padding: 12px; border-left: 3px solid #d8c9b5; background: #faf7f1; }.record-list p { margin: 6px 0; color: #5e5851; }.record-list small { color: #897f75; word-break: break-word; }.claim-meta { display: flex; gap: 6px; }.claim-list .unverified { border-left-color: #d89614; }.claim-list .unresolved { border-left-color: #7f59b0; }.caveat { color: #7a5d3d !important; }.research-form { padding-top: 12px; border-top: 1px solid #e8e1d5; }.form-pair { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.master-workbench { margin-top: 34px; }.master-grid { display: grid; grid-template-columns: 1.25fr .75fr; gap: 16px; }.master-grid :deep(.ant-card), .version-card { background: #fffdf8; border: 1px solid #e8e1d5; box-shadow: none; }.suggestion-actions, .proposal-actions { display: flex; flex-wrap: wrap; gap: 8px; }.proposal-list { display: grid; gap: 10px; margin-top: 16px; }.proposal-list article { padding: 12px; border-left: 3px solid #d8c9b5; background: #faf7f1; }.proposal-meta { display: flex; justify-content: space-between; gap: 8px; color: #948d84; font-size: 12px; }.proposal-copy { max-height: 160px; overflow: auto; white-space: pre-wrap; color: #4e4943; }.version-card { margin-top: 16px; }.version-list { display: grid; gap: 8px; }.version-list article { display: flex; justify-content: space-between; gap: 12px; padding: 10px 0; border-top: 1px solid #e8e1d5; }.version-list span { margin-left: 8px; color: #948d84; font-size: 12px; }.version-list p { margin: 4px 0 0; color: #706b65; }.selection-note { color: #7a6650; font-size: 13px; }
.visual-workbench { margin-top: 34px; }.visual-card { background: #fffdf8; border: 1px solid #e8e1d5; box-shadow: none; }.visual-slot-list { display: grid; gap: 14px; }.visual-slot { padding: 14px; border: 1px solid #e8e1d5; background: #faf7f1; }.slot-heading { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }.visual-actions, .visual-plan-actions, .asset-actions { display: flex; flex-wrap: wrap; gap: 8px; }.visual-plan-actions { margin-top: 16px; }.asset-list { display: grid; gap: 8px; margin-top: 12px; }.visual-asset { padding: 10px; border-left: 3px solid #9db7cc; background: #fffdf8; }.visual-asset.selected { border-left-color: #52a36b; }.visual-asset.failed { border-left-color: #cf5d50; }.visual-asset span { margin-left: 8px; color: #897f75; font-size: 12px; }.visual-asset p { margin: 7px 0; color: #5e5851; white-space: pre-wrap; }.failure { color: #b44336 !important; }
@media (max-width: 640px) { .list-header, .project-row { align-items: flex-start; flex-direction: column; }.project-grid, .research-grid, .form-pair { grid-template-columns: 1fr; }.row-meta { width: 100%; justify-content: space-between; } }
</style>
