<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import { useProjectsStore, useResearchStore, useMasterStore, useVisualsStore, useVariantsStore, useApprovalsStore, type ProjectItem, type ResearchClaim, type MasterSuggestion, type MasterDraftProposal, type VisualSlot, type VisualAsset, type PlatformVariant, type ApprovalCheck, type ProjectExportResult } from '../stores'
import { unwrapError } from '../api/client'
import { formatDateTime } from '../utils/format'
import { renderMarkdown } from '../utils/markdown'
import WechatArticlePreview from '../components/WechatArticlePreview.vue'
import ArticleWorkbench from '../components/ArticleWorkbench.vue'

const route = useRoute()
const router = useRouter()
const store = useProjectsStore()
const researchStore = useResearchStore()
const masterStore = useMasterStore()
const visualsStore = useVisualsStore()
const variantsStore = useVariantsStore()
const approvalsStore = useApprovalsStore()
const { items, loading, error } = storeToRefs(store)
const listItems = computed(() => {
  const titled = items.value
  return titled.filter((item) => {
    const sameTitle = titled.filter(other => other.title === item.title)
    if (sameTitle.length === 1) return true
    const withBody = sameTitle.filter(other => other.has_master)
    if (withBody.length) return item.has_master === true && item.id === withBody[0].id
    return item.id === sameTitle[0].id
  })
})
const { board, loading: researchLoading, error: researchError } = storeToRefs(researchStore)
const { master, suggestions, error: masterError } = storeToRefs(masterStore)
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
const saveStatus = ref('')
let saveTimer: ReturnType<typeof setTimeout> | null = null
watch(() => [masterForm.value.title, masterForm.value.body], () => {
  if (!projectId.value || composing.value || !master.value) return
  if (master.value.title === masterForm.value.title && master.value.body === masterForm.value.body) {
    saveStatus.value = '已保存'
    return
  }
  saveStatus.value = '未保存'
  if (saveTimer) clearTimeout(saveTimer)
  saveTimer = setTimeout(() => { void saveMaster() }, 1600)
})
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
const composing = ref(false)
const composeStage = ref('')
const titleOptions = ref<string[]>([])
const titlesLoading = ref(false)

const unverifiedFacts = computed(() => board.value?.claims.filter(item => item.kind === 'fact' && (item.status === 'unverified' || !item.source_ids.length)) ?? [])
const openQuestions = computed(() => board.value?.claims.filter(item => item.kind === 'open_question' && item.status === 'open') ?? [])
const claimStatusOptions = computed(() => claimForm.value.kind === 'open_question'
  ? [{ value: 'open', label: '待解决' }, { value: 'resolved', label: '已解决' }]
  : [{ value: 'unverified', label: '待核查' }, { value: 'verified', label: '已核查' }])
const wechatVariant = computed(() => variants.value.find(item => item.platform === 'wechat_mp') ?? null)
const focusedPlatform = ref<'wechat_mp'>('wechat_mp')
const preparingPlatforms = ref(false)
const sendingDraft = ref(false)
const draftReceipt = ref<{ media_id: string; title: string; message: string; sent_at: string } | null>(null)
const draftSendError = ref<string | null>(null)
const activeTab = computed(() => {
  if (activeWorkbench.value === 'master') return 'article'
  if (activeWorkbench.value === 'variants') return 'wechat'
  return 'more'
})
const xhsPreview = computed(() => {
  const text = masterForm.value.body.replace(/!\[[^\]]*\]\([^)]+\)/g, '').replace(/^#+\s+/gm, '').trim()
  const images = (visualPlan.value?.assets ?? []).filter(asset => asset.status === 'selected' && visualAssetUrl(asset)).slice(0, 9)
  return {
    title: (masterForm.value.title || project.value?.title || '未命名').slice(0, 20),
    caption: text.slice(0, 220),
    images,
  }
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
    await researchStore.load(projectId.value)
    await masterStore.load(projectId.value)
    await visualsStore.load(projectId.value)
    await variantsStore.load(projectId.value)
    draftReceipt.value = await variantsStore.loadWechatDraft(projectId.value)
    await approvalsStore.load(projectId.value)
    if (master.value) masterForm.value = { title: master.value.title, body: master.value.body }
    else if (project.value) masterForm.value = { title: project.value.title, body: project.value.idea }
    visualBible.value = Object.entries(visualPlan.value?.bible ?? {}).map(([key, value]) => `${key}: ${value}`).join('\n')
    visualSlots.value = visualPlan.value?.slots.map(slot => ({ ...slot })) ?? []
    variantForms.value = Object.fromEntries(variants.value.map(item => [item.platform, { title: item.title, summary: item.summary, body: item.body }]))
    const shouldCompose = route.query.compose === '1' && !(master.value?.body.trim())
    const requested = typeof route.query.focus === 'string' ? route.query.focus : ''
    if (shouldCompose || requested === 'master' || requested === 'article' || requested === 'compose') activeWorkbench.value = 'master'
    else if (requested === 'wechat' || requested === 'variants') activeWorkbench.value = 'variants'
    else if (requested === 'visuals' || requested === 'research' || requested === 'approval') activeWorkbench.value = requested as typeof activeWorkbench.value
    else if (master.value) activeWorkbench.value = 'master'
    else if (variants.value.some(item => item.platform === 'wechat_mp')) activeWorkbench.value = 'variants'
    else activeWorkbench.value = 'master'
    if (shouldCompose) await composeArticle()
  } catch (e) {
    detailError.value = unwrapError(e)
  }
}

async function refreshApprovalStatus(): Promise<void> {
  if (projectId.value) await approvalsStore.load(projectId.value)
}

async function sendWechatDraft(): Promise<void> {
  if (!projectId.value || sendingDraft.value) return
  if (!window.confirm('只送进公众号草稿箱，不会群发。确定现在送吗？')) return
  sendingDraft.value = true
  draftSendError.value = null
  try {
    draftReceipt.value = await variantsStore.sendWechatDraft(projectId.value)
  } catch (e) {
    draftSendError.value = unwrapError(e)
  } finally {
    sendingDraft.value = false
  }
}

async function preparePlatformDrafts(): Promise<void> {
  if (!projectId.value || preparingPlatforms.value) return
  preparingPlatforms.value = true
  try {
    const item = await variantsStore.create(projectId.value, 'wechat_mp', true)
    variantForms.value = { ...variantForms.value, [item.platform]: { title: item.title, summary: item.summary, body: item.body } }
  } catch (error) {
    detailError.value = unwrapError(error)
  } finally {
    preparingPlatforms.value = false
  }
}

function openTab(tab: 'article' | 'wechat' | 'more'): void {
  if (tab === 'article') activeWorkbench.value = 'master'
  else if (tab === 'wechat') { activeWorkbench.value = 'variants'; focusedPlatform.value = 'wechat_mp' }
  else activeWorkbench.value = 'approval'
}

function firstHeading(body: string, fallback: string): string {
  return body.split('\n').find(line => line.startsWith('## '))?.replace(/^##\s+/, '').trim() || fallback
}

function embedSelectedImages(body: string): string {
  const slots = visualPlan.value?.slots ?? []
  const selected = visualPlan.value?.assets.filter(asset => asset.status === 'selected' && visualAssetUrl(asset)) ?? []
  if (!selected.length) return body
  const cover = selected.find(asset => (slots.find(slot => slot.id === asset.slot_id)?.purpose ?? '').includes('封面'))
  const inserts = selected.filter(asset => asset !== cover)
  const imageLine = (asset: VisualAsset): string => {
    const purpose = slots.find(slot => slot.id === asset.slot_id)?.purpose ?? '插图'
    return `![${purpose}](${visualAssetUrl(asset)})`
  }
  const unusedCover = cover && !body.includes(cover.id) ? imageLine(cover) : ''
  const unusedInserts = inserts.filter(asset => !body.includes(asset.id))
  const chunks = [unusedCover, body].filter(Boolean)
  if (!unusedInserts.length) return chunks.join('\n\n')
  const parts = body.split('\n## ')
  if (parts.length < 2) return `${chunks[0] ? `${chunks[0]}\n\n` : ''}${body}\n\n${unusedInserts.map(imageLine).join('\n\n')}`
  const rebuilt: string[] = [unusedCover ? `${unusedCover}\n\n${parts[0].trim()}` : parts[0].trim()]
  parts.slice(1).forEach((part, index) => {
    const image = unusedInserts[index] ? `${imageLine(unusedInserts[index])}\n\n` : ''
    rebuilt.push(`${image}## ${part.trim()}`)
  })
  return rebuilt.join('\n\n')
}

async function generateStandardVisualsForArticle(): Promise<void> {
  if (!projectId.value || !visualProvider.value?.available) return
  setupStandardVisuals()
  visualBible.value = '风格: 克制的编辑插画\n色彩: 暖白纸张与墨色\n文字: 画面内不要出现可阅读文字'
  await saveVisualPlan()
  const title = masterForm.value.title || project.value?.title || '文章'
  const first = firstHeading(masterForm.value.body, '开头的问题')
  const prompts = [
    { match: '封面', prompt: `Editorial illustration, no readable text. Warm cream paper, restrained earth tones. Visual metaphor for: ${title}` },
    { match: '插图一', prompt: `Editorial illustration, no readable text. Warm cream paper. Visualize the article's opening tension: ${first}` },
    { match: '插图二', prompt: `Editorial illustration, no readable text. Warm cream paper. Visualize the article's core judgment: ${title}` },
  ]
  for (const slot of visualSlots.value) {
    const prompt = prompts.find(item => slot.purpose.includes(item.match))?.prompt || slot.direction
    visualPrompts.value = { ...visualPrompts.value, [slot.id]: prompt }
    try {
      const asset = await visualsStore.generate(projectId.value, slot.id, prompt)
      if (asset.status !== 'failed') await visualsStore.select(projectId.value, asset.id, '首页一次生成选用的插图', 4)
    } catch {
      // Keep the article visible; image failure is not a full-page error.
    }
  }
  await visualsStore.load(projectId.value)
  const withImages = embedSelectedImages(masterForm.value.body)
  if (withImages !== masterForm.value.body) {
    masterForm.value = { ...masterForm.value, body: withImages }
    await masterStore.save(projectId.value, masterForm.value)
  }
}

async function composeArticle(): Promise<void> {
  if (!projectId.value || composing.value) return
  composing.value = true
  detailError.value = null
  composeStage.value = '正在根据你的想法起草文章'
  try {
    const article = await masterStore.compose(projectId.value)
    masterForm.value = { title: article.title, body: article.body }
    composeStage.value = '正文已写出，正在按成稿起标题'
    await suggestTitles()
    composeStage.value = '正文已写出，正在生成封面和插图'
    await generateStandardVisualsForArticle()
    composeStage.value = ''
    if (route.query.auto === '1') {
      composeStage.value = '正在准备微信公众号稿'
      await preparePlatformDrafts()
      activeWorkbench.value = 'variants'
      focusedPlatform.value = 'wechat_mp'
    } else {
      activeWorkbench.value = 'master'
    }
    await refreshApprovalStatus()
  } catch (error) {
    const message = unwrapError(error)
    if (message.includes('master_already_exists') && projectId.value) {
      await masterStore.load(projectId.value)
      if (master.value) masterForm.value = { title: master.value.title, body: master.value.body }
    } else {
      detailError.value = message
      if (project.value) {
        if (!masterForm.value.title.trim()) masterForm.value.title = project.value.title
        if (!masterForm.value.body.trim()) masterForm.value.body = project.value.idea
      }
    }
  } finally {
    composing.value = false
    composeStage.value = ''
  }
}

async function suggestTitles(): Promise<void> {
  if (!projectId.value || titlesLoading.value || !masterForm.value.body.trim()) return
  titlesLoading.value = true
  try {
    titleOptions.value = await masterStore.proposeTitles(projectId.value)
  } catch (e) {
    detailError.value = unwrapError(e)
  } finally {
    titlesLoading.value = false
  }
}

async function applyGeneratedTitle(title: string): Promise<void> {
  if (!projectId.value || !title.trim()) return
  try {
    const updated = await masterStore.applyTitle(projectId.value, title.trim())
    masterForm.value = { title: updated.title, body: updated.body }
    if (project.value) project.value = { ...project.value, title: updated.title }
  } catch (e) {
    detailError.value = unwrapError(e)
  }
}

async function saveMaster(): Promise<void> {
  if (!projectId.value) return
  masterSaving.value = true
  try {
    await masterStore.save(projectId.value, masterForm.value)
    saveStatus.value = '已保存'
    await refreshApprovalStatus()
  } catch (e) {
    saveStatus.value = '保存失败'
    detailError.value = unwrapError(e)
  } finally { masterSaving.value = false }
}
function useDraftProposal(): void {
  if (!draftProposal.value) return
  masterForm.value = { ...draftProposal.value }
  draftProposal.value = null
}
async function requestSuggestion(input: { action: MasterSuggestion['action']; selection: string | null; note?: string }): Promise<void> {
  if (!projectId.value) return
  suggestionSaving.value = true
  detailError.value = null
  try {
    if (!master.value || master.value.title !== masterForm.value.title || master.value.body !== masterForm.value.body) {
      await saveMaster()
    }
    await masterStore.request(projectId.value, input)
  } catch (e) {
    detailError.value = unwrapError(e)
  } finally {
    suggestionSaving.value = false
  }
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
function platformName(platform: PlatformVariant['platform']): string { return platform === 'wechat_mp' ? '微信公众号' : platform }
function variantForm(platform: PlatformVariant['platform']): { title: string; summary: string; body: string } { return variantForms.value[platform] ?? { title: '', summary: '', body: '' } }
async function createVariant(platform: PlatformVariant['platform'], adaptWithAi = false): Promise<void> { if (!projectId.value) return; variantGenerating.value = platform; try { const item = await variantsStore.create(projectId.value, platform, adaptWithAi); variantForms.value[item.platform] = { title: item.title, summary: item.summary, body: item.body }; await refreshApprovalStatus() } catch (e) { detailError.value = unwrapError(e) } finally { variantGenerating.value = null } }
async function saveVariant(item: PlatformVariant): Promise<void> { if (!projectId.value) return; try { const form = variantForm(item.platform); await variantsStore.save(projectId.value, item.platform, { ...form, asset_ids: item.asset_ids }); await refreshApprovalStatus() } catch (e) { detailError.value = unwrapError(e) } }
async function toggleLock(item: PlatformVariant): Promise<void> { if (!projectId.value) return; try { await variantsStore.lock(projectId.value, item.platform, !item.locked); await refreshApprovalStatus() } catch (e) { detailError.value = unwrapError(e) } }
async function checkVariantUpstream(item: PlatformVariant): Promise<void> { if (!projectId.value) return; try { await variantsStore.checkUpstream(projectId.value, item.platform) } catch (e) { detailError.value = unwrapError(e) } }
async function acknowledgeVariantMaster(item: PlatformVariant): Promise<void> { if (!projectId.value) return; try { const updated = await variantsStore.acknowledgeMaster(projectId.value, item.platform); variantForms.value[item.platform] = { title: updated.title, summary: updated.summary, body: updated.body }; await refreshApprovalStatus() } catch (e) { detailError.value = unwrapError(e) } }
async function restoreVariant(item: PlatformVariant, version: number): Promise<void> { if (!projectId.value) return; try { const updated = await variantsStore.restore(projectId.value, item.platform, version); variantForms.value[item.platform] = { title: updated.title, summary: updated.summary, body: updated.body }; await refreshApprovalStatus() } catch (e) { detailError.value = unwrapError(e) } }
function previewVariant(item: PlatformVariant): void { if (!projectId.value) return; window.open(`/api/v1/projects/${projectId.value}/variants/${item.platform}/preview`, '_blank', 'noopener') }
function variantPreviewHtml(item: PlatformVariant): string {
  const form = variantForm(item.platform)
  const slots = visualPlan.value?.slots ?? []
  const assets = visualPlan.value?.assets ?? []
  const chosen = item.asset_ids
    .map(id => assets.find(asset => asset.id === id && asset.status === 'selected'))
    .filter((asset): asset is VisualAsset => Boolean(asset))
  const alreadyInBody = (asset: VisualAsset): boolean => {
    const url = visualAssetUrl(asset)
    return Boolean(
      form.body.includes(asset.id)
      || (url && form.body.includes(url))
      || (asset.file_path && form.body.includes(asset.file_path)),
    )
  }
  const covers = chosen.filter(asset => (slots.find(slot => slot.id === asset.slot_id)?.purpose ?? '').includes('封面') && !alreadyInBody(asset))
  const inserts = chosen.filter(asset => !covers.includes(asset) && !alreadyInBody(asset) && !(slots.find(slot => slot.id === asset.slot_id)?.purpose ?? '').includes('封面'))
  const imageLine = (asset: VisualAsset): string => {
    const url = visualAssetUrl(asset)
    const purpose = slots.find(slot => slot.id === asset.slot_id)?.purpose ?? '插图'
    return url ? `![${purpose}](${url})` : ''
  }
  const paragraphs = form.body.split('\n\n').filter(paragraph => paragraph.trim())
  const lines: string[] = [`# ${form.title}`, '', form.summary ? `> ${form.summary}` : '', '']
  for (const cover of covers) lines.push(imageLine(cover), '')
  const pending = [...inserts]
  paragraphs.forEach((paragraph, index) => {
    lines.push(paragraph.trim(), '')
    const anchored = pending.filter(asset => {
      const anchor = slots.find(slot => slot.id === asset.slot_id)?.paragraph_anchor
      return Boolean(anchor && paragraph.includes(anchor))
    })
    const midpoint = Math.max(1, Math.floor(paragraphs.length / 2))
    const picked = anchored.length ? anchored : (pending.length && (index === 0 || index === midpoint) ? [pending[0]] : [])
    for (const asset of picked) {
      lines.push(imageLine(asset), '')
      const at = pending.indexOf(asset)
      if (at >= 0) pending.splice(at, 1)
    }
  })
  for (const asset of pending) lines.push(imageLine(asset), '')
  return renderMarkdown(lines.join('\n'))
}
function downloadVariantMarkdown(item: PlatformVariant): void {
  const form = variantForm(item.platform)
  const blob = new Blob([`# ${form.title}\n\n> ${form.summary}\n\n${form.body}\n`], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `${form.title || platformName(item.platform)}.md`
  link.click()
  URL.revokeObjectURL(url)
}
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
      <button type="button" class="back" @click="router.push('/projects')">全部文章</button>
      <p v-if="detailError" class="banner bad">{{ detailError }}</p>
      <article v-if="project" class="project-workspace">
        <header v-if="activeWorkbench !== 'master' && activeWorkbench !== 'variants'" class="workspace-head">
          <h1>{{ project.title }}</h1>
          <p class="idea">{{ project.idea }}</p>
        </header>
        <div v-show="activeWorkbench !== 'variants' && activeWorkbench !== 'master'" class="project-grid">
          <section>
            <h3>写给谁</h3>
            <p>{{ project.audience }}</p>
            <h3>这次要完成什么</h3>
            <p>{{ project.goal }}</p>
            <h3>声音</h3>
            <p>{{ project.voice }}</p>
          </section>
          <section>
            <h3>目前的材料</h3>
            <p>已关联 {{ project.content_ids.length }} 篇内容，{{ project.asset_paths.length }} 项资产。</p>
            <p class="muted">来源、判断和待确认项都由你录入，不会自动抓取或改写。</p>
          </section>
        </div>
        <nav v-show="!composing" class="surface-tabs" aria-label="文章和平台">
          <button :class="{ active: activeTab === 'article' }" @click="openTab('article')">文章</button>
          <button :class="{ active: activeTab === 'wechat' }" @click="openTab('wechat')">微信</button>
          <button :class="{ active: activeTab === 'more' }" @click="openTab('more')">更多</button>
        </nav>
        <div v-if="activeTab === 'more'" class="more-sub">
          <button :class="{ active: activeWorkbench === 'visuals' }" @click="activeWorkbench = 'visuals'">配图</button>
          <button :class="{ active: activeWorkbench === 'research' }" @click="activeWorkbench = 'research'">资料</button>
          <button :class="{ active: activeWorkbench === 'approval' }" @click="activeWorkbench = 'approval'">交付</button>
        </div>
        <section v-show="activeWorkbench === 'research'" class="research-board">
          <header class="section-heading"><div><h2>资料</h2><p>来源不会自动核查。标记为“已核查”前，请自行确认原始材料。</p></div></header>
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
        <section v-if="activeWorkbench === 'master'" class="master-workbench">
          <header v-if="composing || !master?.body.trim()" class="section-heading"><div><h2>{{ composing ? (composeStage || '正在生成这篇文章') : '文章' }}</h2><p>{{ composing ? '正文会先出现，封面和插图随后补上。' : '点一段文字就地改。不会静默覆盖上一版。' }}</p></div><span v-if="master" class="meta-chip">v{{ master.version }}</span></header>
          <a-alert v-if="masterError" type="error" :message="masterError" show-icon class="notice" />
          <div v-if="composing" class="compose-progress">
            <a-spin />
            <div>
              <strong>{{ composeStage || '正在生成文章' }}</strong>
              <p class="muted">先出正文。标题等文章写完再按成稿挑。</p>
            </div>
          </div>
          <div v-else-if="!master?.body.trim()" class="draft-actions">
            <div><strong>还没有正文</strong><p class="muted">用你在首页写下的想法生成一篇，或自己先写。</p></div>
            <a-button type="primary" :loading="draftGenerating" :disabled="composing" @click="composeArticle">生成文章</a-button>
          </div>
          <a-card v-if="draftProposal" title="待审阅的 AI 初稿" :bordered="false" class="draft-proposal"><h3>{{ draftProposal.title }}</h3><p class="proposal-copy">{{ draftProposal.body }}</p><div class="proposal-actions"><a-button type="primary" @click="useDraftProposal">放入编辑器继续修改</a-button><a-button @click="draftProposal = null">丢弃</a-button></div></a-card>
          <p class="master-count">{{ masterForm.body.trim().length }} 字 · {{ saveStatus || (masterSaving ? '保存中' : '已保存') }}</p>
          <div v-if="master?.body.trim() && !wechatVariant" class="draft-actions">
            <div><strong>准备微信公众号稿</strong><p class="muted">从这篇主稿生成公众号阅读版。只进预览和草稿箱，不会群发。</p></div>
            <a-button type="primary" :loading="preparingPlatforms" @click="preparePlatformDrafts().then(() => openTab('wechat'))">生成微信稿</a-button>
          </div>
          <ArticleWorkbench
            :title="masterForm.title"
            :body="masterForm.body"
            :suggestions="suggestions"
            :suggesting="suggestionSaving"
            :saving="masterSaving"
            :error="masterError"
            :title-options="titleOptions"
            :titles-loading="titlesLoading"
            @update:title="masterForm.title = $event"
            @update:body="masterForm.body = $event"
            @save="saveMaster"
            @request="requestSuggestion"
            @accept="acceptSuggestion"
            @reject="rejectSuggestion"
            @request-titles="suggestTitles"
            @apply-title="applyGeneratedTitle"
          />
          <details v-if="master" class="version-card">
            <summary>历史版本</summary>
            <p class="muted">恢复会生成新版本，不会抹掉现在这篇。</p>
            <div class="version-list"><article v-for="version in [...master.history, { version: master.version, title: master.title, body: master.body, saved_at: master.updated_at, reason: 'current' }]" :key="version.version"><div><strong>版本 {{ version.version }}</strong><span>{{ version.reason === 'current' ? '当前版本' : version.reason }}</span><p>{{ version.body.slice(0, 100) }}{{ version.body.length > 100 ? '…' : '' }}</p></div><a-button v-if="version.version !== master.version" size="small" @click="restoreVersion(version.version)">恢复为新版本</a-button></article></div>
          </details>
        </section>
        <section v-show="activeWorkbench === 'visuals'" class="visual-workbench">
          <header class="section-heading"><div><h2>配图</h2><p>候选不会写入主稿或平台版本。成本是请求前预估。</p></div></header>
          <a-alert v-if="visualsError" type="error" :message="visualsError" show-icon class="notice" />
          <a-alert v-if="visualProvider && !visualProvider.available" type="warning" show-icon class="notice" :message="visualProvider.reason || 'GPT Image 2 暂不可用'" description="可以继续完成视觉计划，并为每个槽位导入本地 PNG；导入会保留提示词、版本和选择记录。" />
          <div class="visual-bootstrap"><a-button v-if="!visualSlots.length" type="primary" @click="setupStandardVisuals">建立 1 张封面 + 2 张插图</a-button><p v-else class="muted">已规划 {{ visualSlots.length }} 个槽位；保存计划后，可以生成或导入候选。</p></div>
          <div v-if="visualSlots.length" class="local-imports"><strong>本地 PNG 兜底</strong><label v-for="slot in visualSlots" :key="`import-${slot.id}`" class="import-button"><span>{{ importingSlot === slot.id ? '正在导入…' : `为「${slot.purpose}」导入 PNG` }}</span><input type="file" accept="image/png" :disabled="importingSlot !== null" @change="importVisual($event, slot)" /></label></div>
          <div v-if="visualPlan?.assets.some(asset => visualAssetUrl(asset))" class="asset-gallery"><figure v-for="asset in visualPlan.assets.filter(item => visualAssetUrl(item))" :key="`preview-${asset.id}`"><img :src="visualAssetUrl(asset) || ''" :alt="asset.prompt" /><figcaption>{{ visualSlots.find(slot => slot.id === asset.slot_id)?.purpose }} · {{ asset.status === 'selected' ? '已选择' : '候选' }}</figcaption></figure></div>
          <a-spin :spinning="visualsLoading"><a-card :bordered="false" class="visual-card"><a-form layout="vertical"><a-form-item label="视觉圣经"><a-textarea v-model:value="visualBible" :rows="3" placeholder="例如：风格: 克制的编辑插画\n色彩: 暖白纸张与墨蓝" /></a-form-item><div class="visual-slot-list"><article v-for="(slot, index) in visualSlots" :key="slot.id" class="visual-slot"><div class="slot-heading"><strong>{{ slot.purpose || `槽位 ${index + 1}` }}</strong><a-button type="link" danger size="small" @click="visualSlots.splice(index, 1)">移除</a-button></div><div class="form-pair"><a-form-item label="用途"><a-input v-model:value="slot.purpose" placeholder="封面 / 正文插图" /></a-form-item><a-form-item label="比例"><a-select v-model:value="slot.aspect_ratio"><a-select-option value="16:9">16:9 横图</a-select-option><a-select-option value="1:1">1:1 方图</a-select-option><a-select-option value="9:16">9:16 竖图</a-select-option><a-select-option value="4:3">4:3</a-select-option><a-select-option value="3:4">3:4</a-select-option></a-select></a-form-item></div><a-form-item label="对应段落（可选）"><a-input v-model:value="slot.paragraph_anchor" placeholder="例如：开头的核心问题" /></a-form-item><a-form-item label="画面方向"><a-textarea v-model:value="slot.direction" :rows="2" placeholder="这张图要帮助读者理解什么？" /></a-form-item><a-form-item label="生成或编辑提示词"><a-textarea v-model:value="visualPrompts[slot.id]" :rows="2" placeholder="显式点击后才会调用 GPT Image 2" /></a-form-item><div class="visual-actions"><a-button :loading="visualGenerating === slot.id" :disabled="!visualProvider?.available || !visualPrompts[slot.id]?.trim()" @click="generateVisual(slot)">生成候选</a-button></div><div v-if="visualPlan?.assets.filter(asset => asset.slot_id === slot.id).length" class="asset-list"><article v-for="asset in visualPlan.assets.filter(item => item.slot_id === slot.id).slice().reverse()" :key="asset.id" :class="['visual-asset', asset.status]"><div><a-tag :color="asset.status === 'selected' ? 'green' : asset.status === 'failed' ? 'red' : 'blue'">{{ asset.status === 'selected' ? '已选择' : asset.status === 'failed' ? '失败' : '候选' }}</a-tag><span>v{{ asset.version }} · {{ asset.model }} · 预估 ${{ asset.cost_usd.toFixed(2) }}</span></div><p>{{ asset.prompt }}</p><p v-if="asset.failure" class="failure">{{ asset.failure }}</p><div v-if="asset.status !== 'failed'" class="asset-actions"><a-button v-if="asset.status !== 'selected'" size="small" @click="selectVisual(asset.id)">选择</a-button><a-button size="small" :loading="visualGenerating === slot.id" :disabled="!visualProvider?.available || !visualPrompts[slot.id]?.trim()" @click="generateVisual(slot, asset.id)">基于此编辑</a-button></div></article></div></article></div><div class="visual-plan-actions"><a-button @click="addVisualSlot">添加插图槽位</a-button><a-button type="primary" :loading="visualSaving" @click="saveVisualPlan">保存视觉计划</a-button></div></a-form></a-card></a-spin>
        </section>
        <section v-if="activeWorkbench === 'variants'" class="variants-workbench">
          <header class="section-heading"><div><h2>微信公众号</h2><p>先看读者会打开的样子。确认后检查配置，再送进草稿箱。不会群发。</p></div></header>
          <a-alert v-if="variantsError" type="error" :message="variantsError" show-icon class="notice" />
          <a-alert type="info" show-icon class="notice" message="公众号只进草稿箱，不会群发。先在本页或设置里保存 AppID / AppSecret，再检查当前 IP 是否在白名单。" />
          <div class="wechat-layout">
          <div v-if="wechatVariant && focusedPlatform === 'wechat_mp'" class="wechat-stage">
            <div>
              <WechatArticlePreview :html="variantPreviewHtml(wechatVariant)" caption="微信公众号阅读预览 · 封面和插图来自已选视觉资产" />
              <div class="wechat-actions">
                <a-button type="primary" :loading="sendingDraft" :disabled="!wechatVariant" @click="sendWechatDraft">送进草稿箱</a-button>
                <a-button @click="previewVariant(wechatVariant)">全屏预览</a-button>
                <a-button @click="downloadVariantMarkdown(wechatVariant)">导出 Markdown</a-button>
              </div>
              <p v-if="draftSendError" class="banner bad">{{ draftSendError }}</p>
              <p v-else-if="draftReceipt" class="banner">{{ draftReceipt.message }} 草稿 media_id：{{ draftReceipt.media_id }}</p>
            </div>
          </div>
          <div>
          <div class="draft-actions">
            <div>
              <strong>公众号配置</strong>
              <p class="muted">AppID / Secret 只存在本机。保存后点「检查连通」验证白名单。不要发到聊天里。</p>
            </div>
            <a-button @click="router.push('/settings')">打开设置填写</a-button>
          </div>
          <div class="variant-adapt">
            <div>
              <strong>微信公众号</strong>
              <a-button type="primary" :loading="variantGenerating === 'wechat_mp' || preparingPlatforms" :disabled="!master" @click="createVariant('wechat_mp', true)">生成或刷新微信稿</a-button>
            </div>
            <p class="muted">先看阅读预览，再去设置里检查公众号连通，然后才能送进草稿箱。</p>
          </div>
          <a-spin :spinning="variantsLoading"><p v-if="!master" class="muted">先有文章，才能生成微信稿。</p><div class="variant-list"><a-card v-for="item in variants.filter(variant => variant.platform === focusedPlatform)" :key="item.platform" :title="platformName(item.platform)" :bordered="false"><template #extra><a-tag v-if="item.locked" color="gold">已锁定</a-tag><a-tag v-if="item.upstream_updated || (master && item.source_master_version !== master.version)" color="orange">主稿有更新</a-tag></template><a-alert v-if="item.upstream_updated || (master && item.source_master_version !== master.version)" type="warning" show-icon message="主稿有更新。先解锁并人工合并必要修改，再点击“确认已合并当前主稿”；系统不会覆盖平台稿。" class="notice"/><a-form layout="vertical"><a-form-item label="标题"><a-input v-model:value="variantForm(item.platform).title" :disabled="item.locked" /></a-form-item><a-form-item label="摘要"><a-textarea v-model:value="variantForm(item.platform).summary" :rows="2" :disabled="item.locked" /></a-form-item><a-form-item label="正文"><a-textarea v-model:value="variantForm(item.platform).body" :rows="8" :disabled="item.locked" /></a-form-item><div class="variant-actions"><a-button type="primary" :disabled="item.locked" @click="saveVariant(item)">保存独立版本</a-button><a-button @click="toggleLock(item)">{{ item.locked ? '解锁编辑' : '锁定版本' }}</a-button><a-button @click="checkVariantUpstream(item)">检查主稿更新</a-button><a-button v-if="master && item.source_master_version !== master.version" :disabled="item.locked" @click="acknowledgeVariantMaster(item)">确认已合并当前主稿 v{{ master.version }}</a-button><a-button @click="previewVariant(item)">打开只读预览</a-button></div><p class="muted">源主稿 v{{ item.source_master_version }} · 平台版本 v{{ item.version }} · {{ item.manually_modified ? '已人工修改' : '尚未人工修改' }}</p><div v-if="item.history.length" class="variant-history"><span>历史版本：</span><a-button v-for="version in item.history" :key="version.version" size="small" :disabled="item.locked" @click="restoreVariant(item, version.version)">恢复 v{{ version.version }}</a-button></div></a-form></a-card></div></a-spin>
          </div>
          </div>
        </section>
        <section v-show="activeWorkbench === 'approval'" class="approval-workbench">
          <header class="section-heading"><div><h2>交付</h2><p>批准只记录你的判断，不会发布或调用微信接口。</p></div><span v-if="approvalStatus?.complete" class="meta-chip on">已完成审批</span></header>
          <a-alert v-if="approvalsError" type="error" :message="approvalsError" show-icon class="notice" />
          <div class="approval-actor"><label for="approval-actor">真实审批人或角色</label><a-input id="approval-actor" v-model:value="approvalActor" placeholder="例如：张三 / Codex 自测（受用户委托）" /></div>
          <a-spin :spinning="approvalsLoading"><a-card :bordered="false" class="approval-card"><a-alert v-if="approvalStatus?.blockers.length" type="warning" show-icon :message="`尚不可审批：${approvalStatus?.blockers.join('；')}`" class="notice"/><a-alert v-else-if="approvalStatus?.stale" type="warning" show-icon message="上游内容已改变，请重新检查。历史批准不会被静默沿用；所有批准与撤回动作已暂停。" class="notice"/><div class="approval-actions"><a-button type="primary" @click="recheckApproval">重新检查内容包</a-button></div><div v-if="approvalStatus?.approval.checks.length" class="approval-list"><article v-for="check in approvalStatus.approval.checks" :key="check.id"><div><strong>{{ approvalLabel[check.id] }}</strong><p>{{ check.status === 'approved' ? `已由 ${check.approved_by} 批准` : '待人工检查' }}</p><small v-if="check.note">当前备注：{{ check.note }}</small></div><div class="approval-decision"><a-input v-model:value="approvalNotes[check.id]" :disabled="!approvalStatus.ready || approvalStatus.stale" placeholder="可选审批备注" size="small"/><a-button v-if="check.status !== 'approved'" type="primary" size="small" :disabled="!approvalStatus.ready || approvalStatus.stale" @click="decideApproval(check, true)">批准</a-button><a-button v-else size="small" :disabled="!approvalStatus.ready || approvalStatus.stale" @click="decideApproval(check, false)">撤回批准</a-button></div></article></div><a-empty v-else description="先重新检查，生成当前内容包的审批清单。" :image-style="{ height: '40px' }"/><p v-if="approvalStatus?.complete" class="approval-complete">所有项目已批准。下一步仅可进入草稿箱或安全导出，仍不等于真实发布。</p><div v-if="approvalStatus?.approval.history.length" class="approval-history"><strong>审批历史</strong><p v-for="event in approvalStatus.approval.history.slice().reverse().slice(0, 8)" :key="`${event.at}-${event.action}-${event.check_id}`">{{ event.at }} · {{ event.actor }} · {{ event.action }}{{ event.check_id ? ` (${approvalLabel[event.check_id]})` : '' }}{{ event.note ? ` · ${event.note}` : '' }}</p></div></a-card></a-spin>
          <section class="xhs-preview">
            <div>
              <strong>小红书形态预告</strong>
              <p>小红书更适合成组照片和短口播感文案。今晚只预览形态，不创建平台稿，也不发布。</p>
            </div>
            <article class="xhs-card">
              <h3>{{ xhsPreview.title }}</h3>
              <p>{{ xhsPreview.caption }}{{ xhsPreview.caption.length >= 220 ? '…' : '' }}</p>
              <div class="xhs-photos">
                <img v-for="asset in xhsPreview.images" :key="asset.id" :src="visualAssetUrl(asset) || ''" :alt="asset.prompt" />
              </div>
            </article>
          </section>
          <div v-if="wechatVariant" class="export-panel">
            <div><strong>这篇微信稿可以交付了</strong><p>先导出 Markdown，或全屏阅读。ZIP 只作备份，不会创建发布记录，也不会调用微信接口。</p></div>
            <div class="wechat-actions">
              <a-button type="primary" @click="downloadVariantMarkdown(wechatVariant)">导出 Markdown</a-button>
              <a-button @click="previewVariant(wechatVariant)">全屏预览</a-button>
              <a-button :loading="exporting" :disabled="!approvalStatus?.complete" @click="exportPackage">备份 ZIP</a-button>
            </div>
            <a v-if="exportResult" :href="exportResult.url" target="_blank" rel="noreferrer">下载 {{ exportResult.file_name }}</a>
          </div>
        </section>
      </article>
    </template>

    <template v-else>
      <header class="list-header">
        <h1>文章</h1>
        <a-button type="primary" @click="router.push('/projects/new')">新文章</a-button>
      </header>
      <p v-if="error" class="banner bad">{{ error }}</p>
      <a-spin :spinning="loading">
        <div v-if="listItems.length" class="project-list">
          <button v-for="item in listItems" :key="item.id" class="project-row" @click="router.push(`/projects/${item.id}`)">
            <div>
              <h2>{{ item.title }}</h2>
              <p>{{ item.idea.length > 90 ? `${item.idea.slice(0, 90)}…` : item.idea }}</p>
              <span>{{ item.has_master ? '已有正文' : '还没写成文章' }} · {{ formatDateTime(item.updated_at) }}</span>
            </div>
          </button>
        </div>
        <p v-else-if="!loading" class="empty-copy">还没有文章。回首页写一个主题即可。</p>
      </a-spin>
    </template>
  </section>
</template>

<style scoped>
.projects-page {
  padding-top: 4px;
}

h1, h2, h3 {
  margin: 0;
  font-weight: 560;
  letter-spacing: -0.03em;
}

h1 {
  font-size: clamp(28px, 4vw, 40px);
  line-height: 1.15;
}

h2 {
  font-size: 20px;
}

h3 {
  margin-bottom: 4px;
  color: var(--faint);
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0;
}

.list-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  margin-bottom: 8px;
}

.idea,
.project-row p,
.project-row span,
.project-workspace p,
.section-heading p,
.muted,
.empty-copy {
  color: var(--muted);
  line-height: 1.65;
}

.banner {
  margin: 12px 0;
  padding: 10px 12px;
  border-radius: var(--radius);
}

.banner.bad {
  background: var(--bad-wash);
  color: var(--bad);
}

.notice {
  margin-bottom: 16px;
}

.project-list {
  border-top: 1px solid var(--line);
}

.project-row {
  width: 100%;
  padding: 20px 0;
  text-align: left;
  border: 0;
  border-bottom: 1px solid var(--line);
  background: transparent;
  cursor: pointer;
}

.project-row h2 {
  margin-bottom: 6px;
  font-size: 18px;
}

.project-row p {
  max-width: 68ch;
  margin: 0 0 6px;
}

.project-row span {
  font-size: 13px;
}

.project-row:hover h2 {
  text-decoration: underline;
  text-underline-offset: 3px;
}

.empty-copy {
  margin-top: 28px;
}

.back {
  margin-bottom: 18px;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--muted);
  cursor: pointer;
}

.back:hover {
  color: var(--ink);
}

.workspace-head {
  max-width: 68ch;
  margin-bottom: 24px;
}

.workspace-head h1 {
  margin-bottom: 8px;
}

.idea {
  font-size: 16px;
}

.project-grid,
.research-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 28px;
  margin-bottom: 8px;
}

.project-grid p {
  margin: 0 0 16px;
}

.research-board,
.master-workbench,
.visual-workbench,
.variants-workbench,
.approval-workbench {
  margin-top: 28px;
}

.section-heading {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: flex-start;
  margin-bottom: 18px;
}

.section-heading p {
  max-width: 52ch;
  margin: 6px 0 0;
}

.meta-chip {
  flex: none;
  padding: 3px 8px;
  border-radius: 4px;
  background: var(--wash);
  color: var(--muted);
  font-size: 12px;
}

.meta-chip.on {
  background: var(--ok-wash);
  color: var(--ok);
}

.research-alerts {
  display: grid;
  gap: 8px;
  margin-bottom: 16px;
}

.record-list {
  display: grid;
  gap: 14px;
  margin-bottom: 20px;
}

.record-list article {
  padding: 0 0 12px;
  border-bottom: 1px solid var(--line);
}

.record-list p {
  margin: 6px 0;
}

.record-list small {
  color: var(--faint);
  word-break: break-word;
}

.claim-meta {
  display: flex;
  gap: 6px;
}

.caveat {
  color: var(--warn) !important;
}

.research-form {
  padding-top: 12px;
  border-top: 1px solid var(--line);
}

.form-pair {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}

.surface-tabs,
.more-sub {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin: 8px 0 0;
  border-bottom: 1px solid var(--line);
}

.surface-tabs button,
.more-sub button {
  padding: 10px 12px;
  border: 0;
  border-bottom: 2px solid transparent;
  background: transparent;
  color: var(--muted);
  cursor: pointer;
}

.surface-tabs button.active,
.more-sub button.active {
  color: var(--ink);
  border-bottom-color: var(--ink);
}

.more-sub {
  margin-top: 0;
  border-bottom: 0;
}

.suggestion-actions,
.proposal-actions,
.variant-actions,
.wechat-actions,
.visual-actions,
.visual-plan-actions,
.asset-actions,
.approval-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.proposal-copy {
  max-height: 160px;
  overflow: auto;
  white-space: pre-wrap;
}

.version-card {
  margin-top: 20px;
  padding-top: 12px;
  border-top: 1px solid var(--line);
}

.version-card summary {
  cursor: pointer;
  color: var(--muted);
}

.version-list article {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 0;
  border-top: 1px solid var(--line);
}

.version-list span {
  margin-left: 8px;
  color: var(--faint);
  font-size: 12px;
}

.draft-actions,
.export-panel,
.variant-adapt,
.local-imports,
.compose-progress {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
  margin: 0 0 18px;
  padding: 14px 0;
  border-top: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
}

.compose-progress strong {
  display: block;
}

.draft-actions p,
.export-panel p,
.compose-progress p {
  margin: 4px 0 0;
}

.draft-proposal .proposal-copy {
  max-height: 360px;
}

.master-count {
  color: var(--faint) !important;
  font-size: 13px;
}

.variant-adapt > div {
  display: flex;
  align-items: center;
  gap: 10px;
}

.variant-adapt > p {
  flex-basis: 100%;
  margin: 0;
}

.local-imports {
  justify-content: flex-start;
}

.import-button {
  display: inline-flex;
  padding: 6px 11px;
  border: 1px dashed var(--line-strong);
  border-radius: 6px;
  color: var(--muted);
  cursor: pointer;
}

.import-button input {
  position: absolute;
  width: 1px;
  height: 1px;
  opacity: 0;
}

.visual-bootstrap {
  margin-bottom: 12px;
}

.asset-gallery {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 16px;
}

.asset-gallery figure {
  margin: 0;
}

.asset-gallery img {
  display: block;
  width: 100%;
  aspect-ratio: 16 / 9;
  object-fit: cover;
}

.asset-gallery figcaption {
  padding-top: 7px;
  color: var(--muted);
  font-size: 12px;
}

.visual-slot-list {
  display: grid;
  gap: 18px;
}

.visual-slot {
  padding: 14px 0;
  border-top: 1px solid var(--line);
}

.slot-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.visual-plan-actions {
  margin-top: 16px;
}

.asset-list {
  display: grid;
  gap: 8px;
  margin-top: 12px;
}

.visual-asset {
  padding: 10px 0;
  border-top: 1px solid var(--line);
}

.visual-asset span {
  margin-left: 8px;
  color: var(--faint);
  font-size: 12px;
}

.visual-asset p {
  margin: 7px 0;
  white-space: pre-wrap;
}

.failure {
  color: var(--bad) !important;
}

.wechat-layout {
  display: grid;
  grid-template-columns: minmax(280px, 400px) minmax(0, 1fr);
  gap: 28px;
  align-items: start;
  margin-bottom: 20px;
}

.wechat-actions {
  align-items: center;
  margin-top: 12px;
}

.variant-list {
  display: grid;
  gap: 16px;
}

.approval-actor {
  display: grid;
  grid-template-columns: 150px minmax(220px, 420px);
  align-items: center;
  gap: 10px;
  margin-bottom: 14px;
  color: var(--muted);
  font-size: 13px;
}

.approval-actions {
  margin-bottom: 14px;
}

.approval-list article {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 0;
  border-top: 1px solid var(--line);
}

.approval-list p,
.approval-list small {
  margin: 4px 0 0;
  color: var(--muted);
}

.approval-decision {
  display: grid;
  grid-template-columns: minmax(130px, 220px) auto;
  gap: 8px;
  align-items: center;
}

.approval-complete {
  margin-top: 16px;
  color: var(--ok) !important;
}

.approval-history {
  margin-top: 18px;
  padding-top: 14px;
  border-top: 1px solid var(--line);
}

.approval-history p {
  margin: 5px 0;
  color: var(--faint);
  font-size: 12px;
}

.xhs-preview {
  margin: 0 0 16px;
  padding: 14px 0;
  border-top: 1px solid var(--line);
}

.xhs-preview p {
  margin: 4px 0 10px;
}

.xhs-card h3 {
  margin: 0 0 8px;
}

.xhs-photos {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 6px;
}

.xhs-photos img {
  width: 100%;
  aspect-ratio: 1;
  object-fit: cover;
}

@media (max-width: 760px) {
  .list-header,
  .project-row,
  .section-heading,
  .approval-list article {
    align-items: flex-start;
    flex-direction: column;
  }

  .project-grid,
  .research-grid,
  .form-pair,
  .wechat-layout,
  .approval-actor,
  .approval-decision {
    grid-template-columns: 1fr;
  }
}
</style>
