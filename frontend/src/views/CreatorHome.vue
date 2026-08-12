<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowRightOutlined, CloseOutlined, FileTextOutlined, HistoryOutlined, LinkOutlined, PaperClipOutlined, ThunderboltOutlined } from '@ant-design/icons-vue'
import { api, unwrapError } from '../api/client'
import type { ProjectItem } from '../stores'

const router = useRouter()
const prompt = ref('')
const starting = ref(false)
const error = ref<string | null>(null)
const recent = ref<ProjectItem[]>([])
const materials = ref<CreatorMaterial[]>([])
const materialInput = ref('')
const materialKind = ref<'url' | 'text'>('url')
const addingMaterial = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)

interface CreatorMaterial {
  id: string
  kind: 'image' | 'url' | 'pdf' | 'markdown' | 'text'
  source: string
  original_name: string | null
  sha256: string
  created_at: string
  status: 'pending' | 'reading' | 'ready' | 'needs_confirmation' | 'failed'
  error: string | null
  stored_path: string | null
}

const DRAFT_KEY = 'mediaforge.creator-home.draft.v1'
const canStart = computed(() => prompt.value.trim().length > 0)
const materialDraftId = ref('')

function saveDraft(): void {
  try { localStorage.setItem(DRAFT_KEY, prompt.value) } catch { /* storage is a convenience, never a blocker */ }
}

function restoreDraft(): void {
  try { prompt.value = localStorage.getItem(DRAFT_KEY) ?? '' } catch { /* ignore unavailable storage */ }
}

function clearDraft(): void {
  try { localStorage.removeItem(DRAFT_KEY) } catch { /* ignore unavailable storage */ }
}

function newDraftId(): string {
  return `draft_${crypto.randomUUID().replace(/-/g, '').slice(0, 8)}`
}

function restoreMaterialDraftId(): void {
  materialDraftId.value = newDraftId()
}

async function loadRecent(): Promise<void> {
  try { recent.value = (await api.get<{ items: ProjectItem[] }>('/projects')).data.items.slice(0, 3) } catch { recent.value = [] }
}

async function startArticle(): Promise<void> {
  if (!canStart.value || starting.value) return
  starting.value = true
  error.value = null
  try {
    const response = await api.post<ProjectItem>('/projects/creator-start', {
      prompt: prompt.value.trim(), draft_id: materialDraftId.value, material_ids: materials.value.filter(item => item.status !== 'failed').map(item => item.id),
    })
    clearDraft()
    materialDraftId.value = newDraftId()
    await router.push(`/projects/${response.data.id}`)
  } catch (cause) {
    error.value = unwrapError(cause)
  } finally {
    starting.value = false
  }
}

async function addTypedMaterial(): Promise<void> {
  if (!materialInput.value.trim() || addingMaterial.value) return
  addingMaterial.value = true
  error.value = null
  try {
    const response = await api.post<CreatorMaterial>('/creator-materials', {
      draft_id: materialDraftId.value, kind: materialKind.value, value: materialInput.value.trim(),
    })
    materials.value = materials.value.some(item => item.id === response.data.id) ? materials.value : [...materials.value, response.data]
    materialInput.value = ''
  } catch (cause) { error.value = unwrapError(cause) } finally { addingMaterial.value = false }
}

async function uploadMaterial(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file || addingMaterial.value) return
  addingMaterial.value = true
  error.value = null
  try {
    const form = new FormData()
    form.append('draft_id', materialDraftId.value)
    form.append('file', file)
    const response = await api.post<CreatorMaterial>('/creator-materials/upload', form)
    materials.value = materials.value.some(item => item.id === response.data.id) ? materials.value : [...materials.value, response.data]
  } catch (cause) { error.value = unwrapError(cause) } finally { addingMaterial.value = false; input.value = '' }
}

async function removeMaterial(material: CreatorMaterial): Promise<void> {
  try {
    await api.delete(`/creator-materials/drafts/${materialDraftId.value}/${material.id}`)
    materials.value = materials.value.filter(item => item.id !== material.id)
  } catch (cause) { error.value = unwrapError(cause) }
}

function materialStatus(item: CreatorMaterial): string {
  return ({ ready: '已读取', needs_confirmation: '需要确认', failed: '读取失败', pending: '待读取', reading: '读取中' })[item.status]
}

watch(prompt, saveDraft)
onMounted(() => { restoreDraft(); restoreMaterialDraftId(); void loadRecent() })
</script>

<template>
  <main class="creator-home" aria-label="开始创作">
    <header class="home-bar">
      <button class="wordmark" type="button" @click="router.push('/')">MediaForge <span>创作</span></button>
      <button class="automation-link" type="button" @click="router.push('/roadmap/automation')">自动化创作 <ArrowRightOutlined /></button>
    </header>

    <section class="home-main">
      <div class="intro">
        <p class="kicker">从一个真实想法开始</p>
        <h1>把一个想法做成文章，也把你想说的说完整。</h1>
        <p>写下一个主题、一段观察，或你此刻说不清的念头。AI 会先为它建一张可继续编辑的创作桌。</p>
      </div>

      <form class="idea-composer" @submit.prevent="startArticle">
        <label for="creator-prompt">你想写什么？</label>
        <textarea
          id="creator-prompt"
          v-model="prompt"
          autofocus
          rows="7"
          placeholder="例如：我每天都在试 AI 工具，但真正困住我的不是工具不够，而是不知道哪一件事值得先做。想从这次做产品却把自己绕进去的经历写起。"
          @keydown.meta.enter.prevent="startArticle"
          @keydown.ctrl.enter.prevent="startArticle"
        />
        <p class="input-help">只要这一项。⌘ Enter 也可以开始；你写到一半的内容会留在这里。</p>
        <section class="materials" aria-label="可选参考资料">
          <div class="materials-heading"><strong>带一点参考，会更像你的文章。</strong><span>可选</span></div>
          <div class="material-actions">
            <button class="file-button" type="button" @click="fileInput?.click()"><PaperClipOutlined /> 添加图片、PDF 或 Markdown</button>
            <input ref="fileInput" class="file-input" type="file" accept="image/png,image/jpeg,image/webp,image/svg+xml,.pdf,.md,.markdown" @change="uploadMaterial" />
            <select v-model="materialKind" aria-label="资料类型"><option value="url">文章链接</option><option value="text">粘贴笔记</option></select>
            <input v-model="materialInput" :placeholder="materialKind === 'url' ? '粘贴文章链接' : '粘贴一段自己的资料'" @keydown.enter.prevent="addTypedMaterial" />
            <button class="add-material" type="button" :disabled="!materialInput.trim() || addingMaterial" @click="addTypedMaterial"><LinkOutlined /> 加入</button>
          </div>
          <div v-if="materials.length" class="material-list">
            <article v-for="item in materials" :key="item.id" class="material-row" :class="`material-${item.status}`">
              <span class="material-kind">{{ item.kind === 'markdown' ? 'MD' : item.kind === 'pdf' ? 'PDF' : item.kind === 'image' ? '图片' : item.kind === 'url' ? '链接' : '笔记' }}</span>
              <span class="material-copy"><strong>{{ item.original_name || item.source }}</strong><small>{{ materialStatus(item) }}<template v-if="item.error">：{{ item.error }}</template></small></span>
              <button type="button" class="remove-material" :aria-label="`移除 ${item.source}`" @click="removeMaterial(item)"><CloseOutlined /></button>
            </article>
          </div>
        </section>
        <p v-if="error" class="form-error" role="alert">{{ error }}</p>
        <div class="composer-footer">
          <span>资料可选；一项失败不会影响你生成文章。</span>
          <button class="generate-button" type="submit" :disabled="!canStart || starting">
            <span v-if="starting">正在建立文章…</span><span v-else>生成文章</span><ThunderboltOutlined />
          </button>
        </div>
      </form>

      <section class="secondary" aria-label="继续创作">
        <div class="section-heading"><div><p class="kicker">最近创作</p><h2>继续你已经开始的文章</h2></div><button type="button" class="all-projects" @click="router.push('/projects')">查看全部 <ArrowRightOutlined /></button></div>
        <div v-if="recent.length" class="recent-list">
          <button v-for="item in recent" :key="item.id" type="button" class="recent-item" @click="router.push(`/projects/${item.id}`)">
            <FileTextOutlined /><span><strong>{{ item.title }}</strong><small>{{ item.idea }}</small></span><ArrowRightOutlined />
          </button>
        </div>
        <div v-else class="empty-recent"><HistoryOutlined /><span>你的第一篇文章，会从上面的想法开始。</span></div>
      </section>
    </section>
  </main>
</template>

<style scoped>
.creator-home { min-height: 100vh; background: #f5f2eb; color: #25231f; }.home-bar { display:flex; height:66px; align-items:center; justify-content:space-between; padding:0 40px; border-bottom:1px solid #ded8cb; background:#fbfaf6; }.wordmark,.automation-link,.generate-button,.all-projects,.recent-item,.file-button,.add-material,.remove-material { border:0; font:inherit; cursor:pointer; }.wordmark { background:transparent; color:#25231f; font-family:Georgia,'Songti SC',serif; font-size:20px; font-weight:700; }.wordmark span { margin-left:5px; color:#a14f32; font-size:12px; }.automation-link,.all-projects { background:transparent; color:#665e54; font-size:14px; }.automation-link :deep(.anticon),.all-projects :deep(.anticon) { margin-left:5px; font-size:11px; }.home-main { width:min(100% - 48px, 880px); margin:0 auto; padding:76px 0 72px; }.intro { max-width:690px; }.kicker { margin:0 0 10px; color:#9f4d31; font-size:12px; font-weight:700; letter-spacing:.07em; }.intro h1 { max-width:700px; margin:0; font-family:Georgia,'Songti SC',serif; font-size:clamp(39px,5vw,62px); line-height:1.1; letter-spacing:-.04em; }.intro > p:last-child { max-width:590px; color:#655f57; font-size:17px; line-height:1.7; }.idea-composer { margin-top:42px; padding:28px; border:1px solid #dcd4c7; border-radius:12px; background:#fffdf8; box-shadow:0 16px 40px rgba(69,58,42,.07); }.idea-composer label { display:block; margin-bottom:9px; color:#3e3a34; font-size:15px; font-weight:700; }.idea-composer textarea { box-sizing:border-box; width:100%; resize:vertical; padding:14px; border:1px solid #cfc7ba; border-radius:8px; outline:0; background:#fffefa; color:#2d2924; font:16px/1.7 -apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif; }.idea-composer textarea:focus { border-color:#9f4d31; box-shadow:0 0 0 3px rgba(159,77,49,.12); }.input-help { margin:8px 0 0; color:#81796f; font-size:12px; }.materials { margin-top:25px; padding-top:21px; border-top:1px solid #e6dfd4; }.materials-heading { display:flex; justify-content:space-between; align-items:center; margin-bottom:11px; font-size:13px; color:#514c44; }.materials-heading span { color:#837a6e; font-size:12px; }.material-actions { display:grid; grid-template-columns:auto 105px minmax(160px,1fr) auto; gap:8px; }.file-button,.add-material { min-height:36px; border-radius:6px; background:#eee8dd; color:#524c43; font-size:12px; padding:0 11px; }.file-button:hover,.add-material:hover:not(:disabled) { background:#e2d9ca; }.add-material:disabled { color:#9d9589; cursor:not-allowed; }.material-actions select,.material-actions input { min-width:0; border:1px solid #d4ccbf; border-radius:6px; background:#fffefa; padding:0 9px; color:#39352f; font:13px -apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif; }.file-input { display:none; }.material-list { display:grid; gap:7px; margin-top:12px; }.material-row { display:flex; min-width:0; align-items:center; gap:9px; padding:8px 9px; border:1px solid #e4ddd1; border-radius:7px; background:#fbfaf6; }.material-kind { flex:0 0 auto; padding:2px 5px; border-radius:4px; background:#eee8dd; color:#6c6257; font-size:10px; font-weight:700; }.material-copy { min-width:0; flex:1; }.material-copy strong,.material-copy small { display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }.material-copy strong { color:#403b34; font-size:12px; }.material-copy small { margin-top:2px; color:#7f776d; font-size:11px; }.material-failed small { color:#a24938; }.remove-material { flex:0 0 auto; padding:4px; background:transparent; color:#80776b; }.remove-material:hover { color:#a24938; }.form-error { margin:13px 0 0; color:#a13e2e; font-size:13px; }.composer-footer { display:flex; align-items:center; justify-content:space-between; gap:20px; margin-top:24px; color:#81796f; font-size:13px; }.generate-button { display:inline-flex; min-height:42px; align-items:center; gap:8px; padding:0 17px; border-radius:7px; background:#2f5d4f; color:#fffdf8; font-weight:700; white-space:nowrap; }.generate-button:hover:not(:disabled) { background:#24493e; }.generate-button:active:not(:disabled) { transform:translateY(1px); }.generate-button:disabled { cursor:not-allowed; background:#bdb7ac; color:#f7f4ed; }.secondary { margin-top:58px; padding-top:25px; border-top:1px solid #dcd4c7; }.section-heading { display:flex; align-items:end; justify-content:space-between; gap:16px; }.section-heading h2 { margin:0; font-family:Georgia,'Songti SC',serif; font-size:25px; }.recent-list { display:grid; margin-top:20px; border-top:1px solid #e5dfd5; }.recent-item { display:grid; grid-template-columns:20px minmax(0,1fr) 16px; align-items:center; gap:13px; padding:15px 2px; border-bottom:1px solid #e5dfd5; background:transparent; color:#454038; text-align:left; }.recent-item:hover { color:#7d4934; }.recent-item span { min-width:0; }.recent-item strong,.recent-item small { display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }.recent-item strong { font-size:14px; }.recent-item small { margin-top:4px; color:#81796f; font-size:12px; }.empty-recent { display:flex; align-items:center; gap:10px; margin-top:20px; color:#81796f; font-size:14px; }@media (max-width:640px) { .home-bar { padding:0 17px; }.home-main { width:min(100% - 34px,880px); padding-top:48px; }.intro h1 { font-size:39px; }.idea-composer { padding:19px; }.material-actions { grid-template-columns:1fr 1fr; }.material-actions input { grid-column:1 / -1; min-height:36px; }.composer-footer { align-items:flex-start; flex-direction:column; }.generate-button { width:100%; justify-content:center; }.section-heading { align-items:flex-start; flex-direction:column; gap:8px; } }
</style>
