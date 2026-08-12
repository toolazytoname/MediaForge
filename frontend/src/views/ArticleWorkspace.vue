<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, GENERATION_TIMEOUT_MS, unwrapError } from '../api/client'
import { renderMarkdown } from '../utils/markdown'

interface MasterVersion { version: number; title: string; body: string; saved_at: string; reason: string }
interface Master { title: string; body: string; version: number; updated_at: string; history: MasterVersion[] }
interface Generation { status: string; completed_images: number; failed_images: number; error: string | null }
interface VisualAsset { id: string; slot_id: string; prompt: string; model: string; file_path: string | null; status: 'candidate' | 'failed' | 'selected'; failure: string | null; created_at: string }
interface VisualPlan { assets: VisualAsset[] }
interface FeedbackProposal { id: string; scope: 'whole_article'; feedback: string; target: string | null; readership: string | null; platform: string | null; values: string | null; status: 'ready' | 'failed'; state: 'current' | 'obsolete'; error: string | null }
interface ProjectMaterial { id: string; kind: string; source: string; original_name: string | null; status: string; error: string | null; analysis: { status: 'used' | 'not_used'; segments: Array<{ citation: string; text: string }> } | null }

const route = useRoute(); const router = useRouter()
const id = computed(() => String(route.params.id))
const master = ref<Master | null>(null); const generation = ref<Generation | null>(null)
const visualPlan = ref<VisualPlan | null>(null); const materials = ref<ProjectMaterial[]>([])
const working = ref(false); const error = ref<string | null>(null); const saving = ref(false)
const secondaryError = ref<string | null>(null)
const drawerWidth = ref(420)
const saveStatus = ref<'saved' | 'unsaved' | 'saving' | 'failed'>('saved')
const lastSaved = ref('')
const editor = ref<HTMLTextAreaElement | null>(null)
const isEditing = ref(false); const moreOpen = ref(false); const detailsOpen = ref(false)
const activeImage = ref<VisualAsset | null>(null); const imagePrompt = ref(''); const imageWorking = ref(false)
const feedbackOpen = ref(false); const feedbackWorking = ref(false); const feedbackError = ref<string | null>(null)
const feedback = ref(''); const feedbackTarget = ref(''); const feedbackReadership = ref(''); const feedbackPlatform = ref(''); const feedbackValues = ref('')
const feedbackProposals = ref<FeedbackProposal[]>([])

const articleHtml = computed(() => master.value ? renderMarkdown(master.value.body) : '')
const progress = computed(() => generation.value?.status === 'preparing_images' ? '正在准备与正文对应的图片…' : '正在理解你的想法并起草文章…')
const versions = computed(() => master.value ? [...master.value.history, { version: master.value.version, title: master.value.title, body: master.value.body, saved_at: master.value.updated_at, reason: '当前版本' }] : [])
const selectedAssets = computed(() => visualPlan.value?.assets.filter(item => item.status === 'selected' && item.file_path) ?? [])
const replacementAssets = computed(() => activeImage.value
  ? visualPlan.value?.assets.filter(item => item.file_path && item.status !== 'failed' && item.slot_id === activeImage.value?.slot_id && item.id !== activeImage.value?.id) ?? []
  : [])

function imageUrl(asset: VisualAsset): string { return `/output/projects/${id.value}/${asset.file_path ?? ''}` }
function imageMarkdown(asset: VisualAsset): string { return `![文章图片](${imageUrl(asset)})` }
function timeLabel(value: string): string { return new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit' }).format(new Date(value)) }

async function loadOptionalContext(): Promise<void> {
  const [visuals, materialResponse] = await Promise.allSettled([
    api.get<VisualPlan>(`/projects/${id.value}/visuals`),
    api.get<{ items: ProjectMaterial[] }>(`/projects/${id.value}/materials`),
  ])
  const issues: string[] = []
  if (visuals.status === 'fulfilled') visualPlan.value = visuals.value.data
  else { visualPlan.value = null; issues.push('图片信息暂时不可用') }
  if (materialResponse.status === 'fulfilled') materials.value = materialResponse.value.data.items
  else { materials.value = []; issues.push('资料信息暂时不可用') }
  secondaryError.value = issues.length ? `${issues.join('；')}。文章仍可阅读和编辑。` : null
}
async function load(): Promise<void> {
  const [article, state] = await Promise.all([
    api.get<{ master: Master | null }>(`/projects/${id.value}/master`),
    api.get<{ generation: Generation | null }>(`/projects/${id.value}/article/generation`),
  ])
  master.value = article.data.master; generation.value = state.data.generation
  await loadOptionalContext()
  try { feedbackProposals.value = (await api.get<{ items: FeedbackProposal[] }>(`/projects/${id.value}/article/feedback`)).data.items } catch { feedbackProposals.value = [] }
  if (master.value) { lastSaved.value = master.value.updated_at; saveStatus.value = 'saved' }
}
function feedbackPayload(): Record<string, string> {
  const values: Record<string, string> = { feedback: feedback.value.trim() }
  for (const [key, value] of Object.entries({ target: feedbackTarget.value, readership: feedbackReadership.value, platform: feedbackPlatform.value, values: feedbackValues.value })) if (value.trim()) values[key] = value.trim()
  return values
}
async function submitWholeArticleFeedback(): Promise<void> {
  if (!feedback.value.trim() || feedbackWorking.value) return
  feedbackWorking.value = true; feedbackError.value = null
  try {
    const proposal = (await api.post<FeedbackProposal>(`/projects/${id.value}/article/feedback`, feedbackPayload(), { timeout: GENERATION_TIMEOUT_MS })).data
    feedbackProposals.value = [proposal, ...feedbackProposals.value]; feedbackOpen.value = false
    feedback.value = ''; feedbackTarget.value = ''; feedbackReadership.value = ''; feedbackPlatform.value = ''; feedbackValues.value = ''
  } catch (cause) { feedbackError.value = unwrapError(cause); await load() } finally { feedbackWorking.value = false }
}
async function retryFeedbackProposal(proposal: FeedbackProposal): Promise<void> {
  if (feedbackWorking.value) return
  feedbackWorking.value = true; feedbackError.value = null
  try { await api.post(`/projects/${id.value}/article/feedback/${proposal.id}/retry`, {}, { timeout: GENERATION_TIMEOUT_MS }); await load() }
  catch (cause) { feedbackError.value = unwrapError(cause) } finally { feedbackWorking.value = false }
}

async function generate(): Promise<void> {
  if (working.value) return
  working.value = true; error.value = null
  try {
    generation.value = (await api.post<Generation>(`/projects/${id.value}/article/generate`, {}, { timeout: GENERATION_TIMEOUT_MS })).data
    await load()
  } catch (cause) { error.value = unwrapError(cause) } finally { working.value = false }
}
async function save(): Promise<boolean> {
  if (!master.value || saving.value) return false
  saving.value = true; saveStatus.value = 'saving'; error.value = null
  try {
    master.value = (await api.put<Master>(`/projects/${id.value}/master`, { title: master.value.title, body: master.value.body })).data
    lastSaved.value = master.value.updated_at; saveStatus.value = 'saved'
    return true
  } catch (cause) {
    error.value = unwrapError(cause); saveStatus.value = 'failed'
    return false
  } finally { saving.value = false }
}
function noteChange(): void {
  saveStatus.value = 'unsaved'
}
async function enterEditor(): Promise<void> { isEditing.value = true; await nextTick(); editor.value?.focus() }
async function retryImages(): Promise<void> {
  if (working.value) return
  working.value = true; error.value = null
  try { generation.value = (await api.post<Generation>(`/projects/${id.value}/article/images/retry`, {}, { timeout: GENERATION_TIMEOUT_MS })).data; await load() }
  catch (cause) { error.value = unwrapError(cause) } finally { working.value = false }
}
async function startManual(): Promise<void> {
  if (working.value || master.value) return
  working.value = true; error.value = null
  try {
    const project = (await api.get<{ title: string; idea: string }>(`/projects/${id.value}`)).data
    master.value = (await api.put<Master>(`/projects/${id.value}/master`, { title: project.title, body: `## 我想说的\n\n${project.idea}\n\n## 接着写` })).data
    lastSaved.value = master.value.updated_at; await enterEditor()
  } catch (cause) { error.value = unwrapError(cause) } finally { working.value = false }
}
async function restoreVersion(version: number): Promise<void> {
  if (saving.value || !master.value || version === master.value.version) return
  saving.value = true; error.value = null
  try { master.value = (await api.post<Master>(`/projects/${id.value}/master/versions/${version}/restore`, {})).data; lastSaved.value = master.value.updated_at; saveStatus.value = 'saved'; moreOpen.value = false }
  catch (cause) { error.value = unwrapError(cause); saveStatus.value = 'failed' } finally { saving.value = false }
}
function findAssetFromImage(target: EventTarget | null): VisualAsset | null {
  const src = target instanceof HTMLImageElement ? target.getAttribute('src') : null
  return visualPlan.value?.assets.find(item => item.file_path && src === imageUrl(item)) ?? null
}
function selectImage(event: MouseEvent): void {
  const asset = findAssetFromImage(event.target)
  if (!asset) return
  activeImage.value = asset; imagePrompt.value = asset.prompt; detailsOpen.value = true
}
function imageFailed(event: Event): void {
  const node = event.target as HTMLImageElement
  node.classList.add('image-broken'); node.alt = '图片暂时无法加载'
}
async function selectAsset(asset: VisualAsset): Promise<void> {
  await api.post(`/projects/${id.value}/visuals/assets/${asset.id}/select`, { reason: '在文章中替换图片' })
  await load()
}
async function replaceImage(asset: VisualAsset): Promise<void> {
  if (!master.value || !activeImage.value) return
  const oldImage = imageMarkdown(activeImage.value)
  if (!master.value.body.includes(oldImage)) { error.value = '当前图片位置已变动，请关闭后重新选择。'; return }
  await selectAsset(asset)
  master.value.body = master.value.body.replace(oldImage, imageMarkdown(asset)); noteChange(); await save()
  activeImage.value = asset; imagePrompt.value = asset.prompt
}
async function editImage(): Promise<void> {
  if (!activeImage.value || !imagePrompt.value.trim() || imageWorking.value) return
  imageWorking.value = true; error.value = null
  try {
    const created = (await api.post<VisualAsset>(`/projects/${id.value}/visuals/assets/edit`, {
      slot_id: activeImage.value.slot_id, prompt: imagePrompt.value.trim(), reference_asset_id: activeImage.value.id,
    }, { timeout: GENERATION_TIMEOUT_MS })).data
    await replaceImage(created)
  } catch (cause) { error.value = `图片修改失败：${unwrapError(cause)}` } finally { imageWorking.value = false }
}
async function removeImage(): Promise<void> {
  if (!master.value || !activeImage.value) return
  master.value.body = master.value.body.replace(`${imageMarkdown(activeImage.value)}\n\n`, '').replace(imageMarkdown(activeImage.value), '')
  noteChange(); await save(); detailsOpen.value = false; activeImage.value = null
}
function viewImageDetails(): void { if (activeImage.value) detailsOpen.value = true }
watch(() => master.value && `${master.value.title}\u0000${master.value.body}`, (value, oldValue) => { if (value && oldValue && value !== oldValue && !saving.value) noteChange() })
onMounted(async () => { drawerWidth.value = Math.max(280, Math.min(420, window.innerWidth - 24)); try { await load(); if (route.query.generate === '1' && !master.value) await generate() } catch (cause) { error.value = unwrapError(cause) } })
</script>

<template>
  <main class="article-workspace" aria-label="文章工作区">
    <header class="topbar">
      <button type="button" class="wordmark" @click="router.push('/')">MediaForge</button>
      <div class="top-actions">
        <span v-if="working" class="progress">{{ progress }}</span>
        <span v-else-if="saveStatus === 'saved' && lastSaved" class="save-state">已保存 {{ timeLabel(lastSaved) }}</span>
        <span v-else-if="saveStatus === 'unsaved'" class="save-state unsaved">有未保存修改</span>
        <button v-if="master" type="button" class="quiet-button" @click="moreOpen = true">资料与版本</button>
        <button v-if="master" type="button" class="quiet-button feedback-entry" @click="feedbackOpen = true">对整篇提意见</button>
        <button v-if="master" type="button" class="save-button" :disabled="saving || saveStatus === 'saved'" @click="save">{{ saving ? '保存中…' : '保存修改' }}</button>
      </div>
    </header>

    <section v-if="working && !master" class="generating"><p>正在把你的想法整理成文章</p><small>正文先完成，封面和插图会接着嵌入对应段落。</small></section>
    <section v-else-if="master" class="article-shell">
      <div v-if="generation?.error" class="local-warning" role="status"><span>{{ generation.error }}</span><button type="button" @click="retryImages">重试未完成图片</button></div>
      <div v-if="secondaryError" class="secondary-warning" role="status">{{ secondaryError }}</div>
      <input v-model="master.title" aria-label="文章标题" class="title" @input="noteChange" />
      <div class="reading-switch"><button type="button" :class="{ active: !isEditing }" @click="isEditing = false">阅读</button><button type="button" :class="{ active: isEditing }" @click="enterEditor">编辑</button></div>
      <article v-if="!isEditing" class="preview" aria-label="文章阅读" v-html="articleHtml" @click="selectImage" @error.capture="imageFailed" />
      <section v-else class="editor-panel"><label for="article-body">编辑 Markdown</label><textarea id="article-body" ref="editor" v-model="master.body" rows="24" @input="noteChange" /><p>你可以直接写；保存失败，内容仍在编辑器中。</p></section>
      <p v-if="saveStatus === 'failed'" class="inline-error" role="alert">保存失败，内容仍在编辑器中。{{ error }}</p>
      <p v-else-if="error" class="inline-error" role="alert">{{ error }}</p>
      <aside v-if="feedbackProposals.length" class="proposal-notice" aria-live="polite"><strong>提案状态</strong><template v-for="proposal in feedbackProposals" :key="proposal.id"><p v-if="proposal.status === 'ready' && proposal.state === 'current'">提案已生成，正式文章尚未修改。</p><p v-else-if="proposal.status === 'ready'">这份提案基于旧版本，文章已更新。</p><p v-else>提案暂未生成：{{ proposal.error }} <button type="button" :disabled="feedbackWorking" @click="retryFeedbackProposal(proposal)">重试生成提案</button></p></template></aside>
    </section>
    <section v-else class="failed"><h1>文章还没有生成</h1><p>{{ error || '你的想法仍在项目里；你可以重试，或直接开始手写。' }}</p><div class="failed-actions"><button type="button" :disabled="working" @click="generate">重试生成文章</button><button type="button" class="manual" :disabled="working" @click="startManual">直接开始手写</button></div></section>

    <a-drawer v-model:open="moreOpen" title="资料与版本" placement="right" :width="drawerWidth">
      <section class="drawer-section"><h3>版本</h3><p>每次保存都保留为可恢复版本。</p><ol class="versions"><li v-for="version in versions.slice().reverse()" :key="version.version"><div><strong>版本 {{ version.version }}</strong><small>{{ version.reason }} · {{ timeLabel(version.saved_at) }}</small></div><button v-if="version.version !== master?.version" type="button" @click="restoreVersion(version.version)">恢复</button></li></ol></section>
      <section class="drawer-section"><h3>资料</h3><p v-if="!materials.length">这篇文章没有附加资料。</p><ul v-else class="materials"><li v-for="item in materials" :key="item.id"><strong>{{ item.original_name || item.source }}</strong><small>{{ item.analysis?.status === 'used' ? '已用于创作' : item.error || '尚未读取' }}</small></li></ul></section>
      <section class="drawer-section"><h3>图片</h3><p>图片的来源、提示词和失败信息都在这里；它们不会打断文章阅读。</p><button v-for="asset in selectedAssets" :key="asset.id" class="image-row" type="button" @click="activeImage = asset; viewImageDetails()">{{ asset.prompt }}</button></section>
    </a-drawer>

    <a-drawer v-model:open="feedbackOpen" title="对整篇文章提意见" placement="right" :width="drawerWidth">
      <section class="feedback-form"><p class="scope">作用范围：<strong>整篇文章</strong></p><p>提案不会修改正文；AI 只会生成提案，不会直接改写正式文章。</p><label for="whole-feedback">你希望怎么改？</label><textarea id="whole-feedback" v-model="feedback" rows="6" placeholder="例如：减少说教感，保留真实失败" /><label for="feedback-target">希望达到的效果（可选）</label><input id="feedback-target" v-model="feedbackTarget" placeholder="例如：更真诚、更有行动感" /><label for="feedback-readership">读者（可选）</label><input id="feedback-readership" v-model="feedbackReadership" placeholder="例如：正在尝试 AI 的普通上班族" /><label for="feedback-platform">平台（可选）</label><input id="feedback-platform" v-model="feedbackPlatform" placeholder="例如：微信公众号" /><label for="feedback-values">价值取向（可选）</label><input id="feedback-values" v-model="feedbackValues" placeholder="例如：不制造焦虑，不夸大效果" /><p v-if="feedbackError" class="inline-error">{{ feedbackError }}</p><button class="save-button" type="button" :disabled="feedbackWorking || !feedback.trim()" @click="submitWholeArticleFeedback">{{ feedbackWorking ? '正在生成可审阅提案…' : '生成可审阅提案' }}</button></section>
    </a-drawer>

    <a-drawer v-model:open="detailsOpen" title="图片详情" placement="right" :width="drawerWidth">
      <template v-if="activeImage"><img v-if="activeImage.file_path" class="detail-image" :src="imageUrl(activeImage)" alt="文章图片" @error="imageFailed" /><p class="image-meta">{{ activeImage.model }} · {{ activeImage.created_at }}</p><label for="image-prompt">怎么改这张图？</label><textarea id="image-prompt" v-model="imagePrompt" rows="4" /><div class="image-actions"><button type="button" class="save-button" :disabled="imageWorking || !imagePrompt.trim()" @click="editImage">{{ imageWorking ? '正在修改…' : '修改图片' }}</button><button type="button" class="danger-button" @click="removeImage">从文章移除</button></div><div v-if="replacementAssets.length" class="replacement-list"><h3>换成已有候选</h3><button v-for="asset in replacementAssets" :key="asset.id" type="button" @click="replaceImage(asset)">{{ asset.prompt }}</button></div><p v-if="activeImage.failure" class="inline-error">{{ activeImage.failure }}</p></template>
    </a-drawer>
  </main>
</template>

<style scoped>
.article-workspace{min-height:100vh;background:#f5f1e9;color:#28251f}.topbar{position:sticky;top:0;z-index:5;display:flex;min-height:62px;align-items:center;justify-content:space-between;gap:16px;padding:0 clamp(18px,5vw,72px);border-bottom:1px solid #ded7cb;background:rgba(255,253,248,.94);backdrop-filter:blur(12px)}button{font:inherit;cursor:pointer}.wordmark{border:0;background:transparent;color:#342d26;font:700 20px Georgia,'Songti SC',serif}.top-actions{display:flex;align-items:center;gap:10px}.progress,.save-state{color:#72695e;font-size:13px}.unsaved{color:#9a542e}.quiet-button,.save-button,.local-warning button,.failed button,.versions button,.image-actions button,.replacement-list button{border:0;border-radius:7px;padding:8px 11px}.quiet-button{background:transparent;color:#5f584f}.feedback-entry{color:#6c432e}.save-button,.local-warning button,.failed button{background:#2f5d4f;color:#fffdf8}.save-button:disabled{cursor:not-allowed;background:#bcb4a8}.generating,.failed{max-width:620px;margin:16vh auto;padding:40px;border:1px solid #dfd8cb;border-radius:12px;background:#fffdfa}.generating p{font:32px Georgia,serif;margin:0 0 12px}.generating small{color:#756d63}.article-shell{width:min(820px,calc(100% - 36px));margin:44px auto 88px}.local-warning,.secondary-warning{display:flex;align-items:center;justify-content:space-between;gap:15px;margin-bottom:16px;padding:11px 13px;border:1px solid #e5c4ae;border-radius:8px;background:#fff8f2;color:#8c4429;font-size:13px}.secondary-warning{border-color:#d9d5cd;background:#faf9f5;color:#756b5f}.title{box-sizing:border-box;width:100%;margin:0 0 16px;border:0;border-bottom:1px solid #d8d0c5;outline:0;background:transparent;padding:0 0 15px;font:clamp(35px,5vw,58px)/1.12 Georgia,'Songti SC',serif;color:#29251e}.title:focus{border-color:#9c522f}.reading-switch{display:flex;gap:4px;margin-bottom:18px}.reading-switch button{border:0;border-radius:5px;background:transparent;padding:6px 10px;color:#777065;font-size:13px}.reading-switch button.active{background:#e7ddd0;color:#433a30;font-weight:700}.preview{min-height:460px;background:#fffdfa;border:1px solid #e2dbd0;border-radius:12px;padding:clamp(24px,6vw,70px);font:18px/1.9 Georgia,'Songti SC',serif;box-shadow:0 15px 45px rgba(79,61,38,.05)}.preview :deep(h2){margin-top:2em;font-size:28px}.preview :deep(p){margin:1em 0}.preview :deep(img){display:block;max-width:100%;margin:28px auto;border-radius:7px;cursor:pointer}.preview :deep(img.image-broken){min-height:180px;outline:1px dashed #b35b42;background:#fff5ef}.editor-panel{background:#fffdfa;border:1px solid #e2dbd0;border-radius:12px;padding:22px}.editor-panel label,.feedback-form label{display:block;margin:14px 0 9px;color:#665e54;font-size:13px;font-weight:700}.editor-panel textarea,.image-actions+*,#image-prompt,.feedback-form textarea,.feedback-form input{box-sizing:border-box;width:100%;border:1px solid #d8d0c3;border-radius:8px;background:#fffdfa;padding:15px;font:14px/1.7 ui-monospace,SFMono-Regular,monospace;color:#2d2924}.editor-panel textarea:focus,#image-prompt:focus,.feedback-form textarea:focus,.feedback-form input:focus{outline:2px solid rgba(159,77,49,.24);border-color:#9f4d31}.editor-panel p,.feedback-form p{margin:9px 0 0;color:#776f65;font-size:12px}.feedback-form .scope{font-size:14px;color:#4f453a}.feedback-form .save-button{margin-top:20px}.proposal-notice{margin:18px 0;padding:12px 14px;border-left:3px solid #b28257;background:#fbf6ef;color:#65594e;font-size:13px}.proposal-notice p{margin:5px 0}.proposal-notice button{border:0;background:transparent;color:#7c432a;text-decoration:underline}.inline-error{margin:16px 0;color:#a44130}.failed-actions{display:flex;gap:10px}.failed button.manual,.danger-button{background:#eee4d8;color:#5b382e}.drawer-section{padding:0 0 22px;margin-bottom:20px;border-bottom:1px solid #e8e1d6}.drawer-section h3{margin:0 0 6px;font:700 19px Georgia,'Songti SC',serif}.drawer-section>p{color:#766e63;font-size:13px;line-height:1.6}.versions,.materials{display:grid;gap:9px;margin:14px 0 0;padding:0;list-style:none}.versions li{display:flex;align-items:center;justify-content:space-between;gap:12px}.versions strong,.versions small,.materials strong,.materials small{display:block}.versions small,.materials small{margin-top:3px;color:#80776c;font-size:12px}.versions button{background:#eee7db;color:#51473c}.image-row,.replacement-list button{display:block;width:100%;margin-top:8px;border:1px solid #e2dad0;border-radius:7px;background:#fffdfa;padding:10px;text-align:left;color:#51483e;font-size:12px}.detail-image{width:100%;border-radius:9px;background:#f0ebe2}.image-meta{color:#80776c;font-size:12px}.image-actions{display:flex;gap:9px;margin-top:14px}.replacement-list h3{margin:24px 0 2px;font:700 16px Georgia,'Songti SC',serif}@media(max-width:640px){.topbar{align-items:flex-start;min-height:unset;padding-top:13px;padding-bottom:13px}.top-actions{justify-content:flex-end;flex-wrap:wrap}.progress,.save-state{width:100%;text-align:right}.article-shell{margin-top:28px}.local-warning{align-items:flex-start;flex-direction:column}.local-warning button{width:100%}.preview{min-height:360px;padding:25px 21px;font-size:17px}.title{font-size:38px}.image-actions{flex-direction:column}.image-actions button{width:100%}}
</style>
