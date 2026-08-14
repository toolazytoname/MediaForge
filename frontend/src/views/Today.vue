<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import { ArrowRightOutlined } from '@ant-design/icons-vue'
import { useProjectsStore, useVariantsStore } from '../stores'
import { unwrapError } from '../api/client'
import { formatDateTime } from '../utils/format'
import homeDesk from '../assets/home-desk.jpg'

const DRAFT_KEY = 'mediaforge.home.draft'

const router = useRouter()
const projectsStore = useProjectsStore()
const variantsStore = useVariantsStore()
const { items, loading, error } = storeToRefs(projectsStore)
const latestProject = computed(() => items.value[0] ?? null)
const topic = ref('')
const idea = ref('')
const starting = ref(false)
const startError = ref<string | null>(null)
const latestHasWechat = ref(false)
const readyWechat = ref<typeof latestProject.value>(null)

const canStart = computed(() => Boolean(topic.value.trim() || idea.value.trim()))

onMounted(async () => {
  try {
    const raw = localStorage.getItem(DRAFT_KEY)
    if (raw) {
      const draft = JSON.parse(raw) as { topic?: string; idea?: string }
      topic.value = draft.topic ?? ''
      idea.value = draft.idea ?? ''
    }
  } catch {
    localStorage.removeItem(DRAFT_KEY)
  }
  await projectsStore.load()
  for (const project of items.value.slice(0, 6)) {
    await variantsStore.load(project.id)
    const wechat = variantsStore.variants.find(item => item.platform === 'wechat_mp' && item.body.trim().length >= 600)
    if (wechat && !readyWechat.value) readyWechat.value = project
    if (latestProject.value && project.id === latestProject.value.id) {
      latestHasWechat.value = variantsStore.variants.some(item => item.platform === 'wechat_mp')
    }
  }
})

watch([topic, idea], () => {
  localStorage.setItem(DRAFT_KEY, JSON.stringify({ topic: topic.value, idea: idea.value }))
})

function titleFromInput(): string {
  const heading = topic.value.trim()
  if (heading) return heading
  const firstLine = idea.value.trim().split('\n').find(line => line.trim()) ?? ''
  return firstLine.slice(0, 36) || '未命名文章'
}

async function startArticle(): Promise<void> {
  if (!canStart.value || starting.value) return
  starting.value = true
  startError.value = null
  try {
    const project = await projectsStore.create({
      title: titleFromInput(),
      idea: idea.value.trim() || topic.value.trim(),
      audience: '27—39 岁左右、正在用 AI 重建工作方式的知识工作者',
      goal: '完成一篇可在微信公众号发布的图文草稿',
      voice: '第一人称、诚实克制、具体、不喊口号',
      autonomy: 'draft',
    })
    localStorage.removeItem(DRAFT_KEY)
    await router.push(`/projects/${project.id}?focus=master`)
  } catch (err) {
    startError.value = unwrapError(err)
  } finally {
    starting.value = false
  }
}

function openProject(id: string, focus: 'master' | 'wechat' = 'master'): void {
  router.push(`/projects/${id}?focus=${focus}`)
}
function openLatest(focus: 'master' | 'wechat' = 'master'): void {
  if (!latestProject.value) return
  openProject(latestProject.value.id, focus)
}

function onMetaEnter(event: KeyboardEvent): void {
  if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
    event.preventDefault()
    void startArticle()
  }
}
</script>

<template>
  <section class="home">
    <header class="hero">
      <img :src="homeDesk" alt="" class="hero-art" />
      <div class="hero-copy">
        <p class="eyebrow">个人创作</p>
        <h1>写一篇完整的图文文章。</h1>
        <p>先写下主题和你自己的想法。不必先建项目、选模式或维护研究板。</p>
      </div>
    </header>

    <a-alert v-if="error" type="error" :message="error" show-icon class="notice" />
    <a-alert v-if="startError" type="error" :message="startError" show-icon class="notice" />

    <a-card :bordered="false" class="compose-card">
      <label class="field">
        <span>主题</span>
        <a-input
          v-model:value="topic"
          placeholder="例如：为什么测试全绿，我还是不敢用自己的产品"
          @keydown="onMetaEnter"
        />
      </label>
      <label class="field">
        <span>你的想法</span>
        <a-textarea
          v-model:value="idea"
          :auto-size="{ minRows: 5, maxRows: 10 }"
          placeholder="这段话可以不完整。写下你真正想说的判断、经历或还没想清楚的问题。"
          @keydown="onMetaEnter"
        />
      </label>
      <div class="compose-actions">
        <a-button type="primary" size="large" :loading="starting" :disabled="!canStart" @click="startArticle">
          开始写这篇文章
        </a-button>
        <span>⌘/Ctrl + Enter</span>
      </div>
    </a-card>

    <a-card v-if="readyWechat && readyWechat.id !== latestProject?.id" :bordered="false" class="continue-card ready-card">
      <div>
        <p class="eyebrow">可阅读的微信稿</p>
        <h2>{{ readyWechat.title }}</h2>
        <p>{{ readyWechat.idea }}</p>
      </div>
      <div class="continue-actions">
        <a-button type="primary" @click="openProject(readyWechat.id, 'wechat')">打开完整微信稿 <ArrowRightOutlined /></a-button>
      </div>
    </a-card>

    <a-card v-if="latestProject" :bordered="false" class="continue-card">
      <div>
        <p class="eyebrow">继续上次</p>
        <h2>{{ latestProject.title }}</h2>
        <p>{{ latestProject.idea }}</p>
        <span>上次更新于 {{ formatDateTime(latestProject.updated_at) }}</span>
      </div>
      <div class="continue-actions">
        <a-button type="primary" @click="openLatest(latestHasWechat ? 'wechat' : 'master')">
          {{ latestHasWechat ? '打开微信稿' : '打开文章' }}
          <ArrowRightOutlined />
        </a-button>
        <a-button v-if="latestHasWechat" @click="openLatest('master')">回主稿</a-button>
      </div>
    </a-card>

    <p v-else-if="!loading" class="secondary">最近文章会显示在这里。灵感、项目列表和自动化入口都是次级路径。</p>
  </section>
</template>

<style scoped>
.home { max-width: 880px; padding: 24px 0 64px; }
.hero { display: grid; grid-template-columns: minmax(0, 1.1fr) minmax(220px, .9fr); gap: 28px; align-items: center; margin-bottom: 24px; }
.hero-art { width: 100%; height: 180px; object-fit: cover; border-radius: 16px; }
.eyebrow { margin: 0 0 8px; color: #7a6650; font-size: 12px; font-weight: 700; letter-spacing: .09em; text-transform: uppercase; }
h1, h2 { color: #292522; font-family: Georgia, 'Songti SC', serif; }
h1 { margin: 0 0 12px; font-size: clamp(32px, 4vw, 46px); line-height: 1.16; }
h2 { margin: 0 0 8px; font-size: 22px; }
.hero-copy p, .continue-card p, .secondary { color: #706b65; font-size: 16px; line-height: 1.7; }
.notice { margin-bottom: 16px; }
.compose-card, .continue-card { margin-bottom: 16px; border: 1px solid #e8e1d5; background: #fffdf8; box-shadow: none; }
.field { display: grid; gap: 8px; margin-bottom: 16px; color: #5c564f; font-size: 13px; font-weight: 600; }
.compose-actions, .continue-actions { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.compose-actions span, .continue-card span { color: #948d84; font-size: 13px; }
.continue-card :deep(.ant-card-body) { display: flex; justify-content: space-between; gap: 24px; align-items: center; }
.secondary { margin-top: 24px; }
@media (max-width: 760px) {
  .hero, .continue-card :deep(.ant-card-body) { grid-template-columns: 1fr; display: grid; }
  .hero-art { height: 140px; }
}
</style>
