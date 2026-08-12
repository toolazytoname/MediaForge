<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, GENERATION_TIMEOUT_MS, unwrapError } from '../api/client'
import { renderMarkdown } from '../utils/markdown'

interface Master { title: string; body: string; version: number }
interface Generation { status: string; completed_images: number; failed_images: number; error: string | null }
const route = useRoute(); const router = useRouter()
const id = computed(() => String(route.params.id))
const master = ref<Master | null>(null); const generation = ref<Generation | null>(null)
const working = ref(false); const error = ref<string | null>(null); const saving = ref(false)
const articleHtml = computed(() => master.value ? renderMarkdown(master.value.body) : '')
const progress = computed(() => generation.value?.status === 'preparing_images' ? '正在准备与正文对应的图片…' : '正在理解你的想法并起草文章…')

async function load(): Promise<void> {
  const [article, state] = await Promise.all([
    api.get<{ master: Master | null }>(`/projects/${id.value}/master`),
    api.get<{ generation: Generation | null }>(`/projects/${id.value}/article/generation`),
  ])
  master.value = article.data.master; generation.value = state.data.generation
}
async function generate(): Promise<void> {
  if (working.value) return
  working.value = true; error.value = null
  try {
    const response = await api.post<Generation>(`/projects/${id.value}/article/generate`, {}, { timeout: GENERATION_TIMEOUT_MS })
    generation.value = response.data
    await load()
  } catch (cause) { error.value = unwrapError(cause) } finally { working.value = false }
}
async function save(): Promise<void> {
  if (!master.value || saving.value) return
  saving.value = true; error.value = null
  try { master.value = (await api.put<Master>(`/projects/${id.value}/master`, { title: master.value.title, body: master.value.body })).data }
  catch (cause) { error.value = unwrapError(cause) } finally { saving.value = false }
}
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
  } catch (cause) { error.value = unwrapError(cause) } finally { working.value = false }
}
onMounted(async () => { try { await load(); if (route.query.generate === '1' && !master.value) await generate() } catch (cause) { error.value = unwrapError(cause) } })
</script>

<template>
  <main class="article-workspace" aria-label="文章工作区">
    <header><button type="button" @click="router.push('/')">MediaForge 创作</button><span v-if="working" class="progress">{{ progress }}</span><span v-else-if="generation?.error" class="warning">{{ generation.error }} <button type="button" class="retry-image" @click="retryImages">重试图片</button></span></header>
    <section v-if="working && !master" class="generating"><p>正在把你的想法整理成文章</p><small>正文先完成，封面和插图会接着嵌入对应段落。</small></section>
    <section v-else-if="master" class="article-shell">
      <div class="article-toolbar"><span>可直接编辑 · {{ generation?.completed_images ?? 0 }} 张图片已完成</span><button type="button" :disabled="saving" @click="save">{{ saving ? '保存中…' : '保存修改' }}</button></div>
      <input v-model="master.title" aria-label="文章标题" class="title" />
      <article class="preview" v-html="articleHtml" />
      <label class="edit-label" for="article-body">编辑 Markdown</label><textarea id="article-body" v-model="master.body" rows="16" />
    </section>
    <section v-else class="failed"><h1>文章还没有生成</h1><p>{{ error || '你的想法仍在项目里；你可以重试，或直接开始手写。' }}</p><div class="failed-actions"><button type="button" :disabled="working" @click="generate">重试生成文章</button><button type="button" class="manual" :disabled="working" @click="startManual">直接开始手写</button></div></section>
    <p v-if="error && master" class="error" role="alert">{{ error }}</p>
  </main>
</template>

<style scoped>
.article-workspace{min-height:100vh;background:#f6f3ed;color:#292720}.article-workspace>header{height:62px;display:flex;align-items:center;justify-content:space-between;padding:0 5vw;border-bottom:1px solid #ded8cc;background:#fffdfa}.article-workspace header button{border:0;background:none;font:700 18px Georgia,serif;color:#403a31;cursor:pointer}.progress{color:#2f5d4f;font-size:14px}.warning,.error{color:#9e4734}.retry-image{margin-left:8px!important;color:#9e4734!important;text-decoration:underline}.generating,.failed{max-width:620px;margin:16vh auto;padding:40px;border:1px solid #dfd8cb;border-radius:12px;background:#fffdfa}.generating p{font:32px Georgia,serif;margin:0 0 12px}.generating small{color:#756d63}.article-shell{width:min(920px,92vw);margin:36px auto 70px}.article-toolbar{display:flex;justify-content:space-between;align-items:center;color:#786f64;font-size:13px}.article-toolbar button,.failed button{border:0;border-radius:6px;padding:9px 14px;background:#2f5d4f;color:#fff;cursor:pointer}.failed-actions{display:flex;gap:10px}.failed button.manual{background:#e8e1d4;color:#443d33}.title{box-sizing:border-box;width:100%;margin:26px 0 18px;border:0;border-bottom:1px solid #ded6ca;background:transparent;padding:0 0 13px;font:46px/1.15 Georgia,'Songti SC',serif;color:#28251f}.preview{background:#fffdfa;border:1px solid #e2dcd0;border-radius:10px;padding:clamp(24px,5vw,62px);font:17px/1.85 Georgia,'Songti SC',serif}.preview :deep(h2){margin-top:2em;font-size:27px}.preview :deep(img){display:block;max-width:100%;margin:26px auto;border-radius:6px}.edit-label{display:block;margin:30px 0 8px;color:#756d63;font-size:13px}.article-shell textarea{box-sizing:border-box;width:100%;padding:15px;border:1px solid #d8d0c3;border-radius:8px;background:#fffdfa;font:14px/1.65 ui-monospace,monospace}.error{width:min(920px,92vw);margin:0 auto;padding-bottom:32px}@media(max-width:640px){.title{font-size:35px}.preview{padding:24px}.article-workspace>header{padding:0 18px}}
</style>
