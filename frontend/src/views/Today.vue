<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import { useProjectsStore, useVariantsStore, type ProjectItem } from '../stores'
import { unwrapError } from '../api/client'
import { formatDateTime } from '../utils/format'

const DRAFT_KEY = 'mediaforge.home.draft'

const router = useRouter()
const projectsStore = useProjectsStore()
const variantsStore = useVariantsStore()
const { items, loading, error } = storeToRefs(projectsStore)
const latestProject = computed(() => items.value[0] ?? null)
const topic = ref('')
const idea = ref('')
const notes = ref('')
const starting = ref(false)
const startError = ref<string | null>(null)
const latestHasWechat = ref(false)
const readyWechat = ref<ProjectItem | null>(null)

const canStart = computed(() => Boolean(topic.value.trim() || idea.value.trim()))

onMounted(async () => {
  try {
    const raw = localStorage.getItem(DRAFT_KEY)
    if (raw) {
      const draft = JSON.parse(raw) as { topic?: string; idea?: string; notes?: string }
      topic.value = draft.topic ?? ''
      idea.value = draft.idea ?? ''
      notes.value = draft.notes ?? ''
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

watch([topic, idea, notes], () => {
  localStorage.setItem(DRAFT_KEY, JSON.stringify({ topic: topic.value, idea: idea.value, notes: notes.value }))
})

function titleFromInput(): string {
  const heading = topic.value.trim()
  if (heading) return heading
  const firstLine = idea.value.trim().split('\n').find(line => line.trim()) ?? ''
  return firstLine.slice(0, 36) || '未命名文章'
}

async function startArticle(mode: 'review' | 'auto' = 'review'): Promise<void> {
  if (!canStart.value || starting.value) return
  starting.value = true
  startError.value = null
  try {
    const title = titleFromInput()
    const ideaText = [idea.value.trim() || topic.value.trim(), notes.value.trim() ? `作者提供的资料：\n${notes.value.trim()}` : ''].filter(Boolean).join('\n\n')
    const reusable = items.value.find(item => !item.has_master && item.title === title)
    const project = reusable ?? await projectsStore.create({
      title,
      idea: ideaText,
      audience: '27—39 岁左右、正在用 AI 重建工作方式的知识工作者',
      goal: '完成一篇可在微信公众号发布的图文草稿',
      voice: '第一人称、诚实克制、具体、不喊口号',
      autonomy: mode === 'auto' ? 'pack' : 'draft',
    })
    localStorage.removeItem(DRAFT_KEY)
    await router.push(mode === 'auto' ? `/projects/${project.id}?compose=1&auto=1` : `/projects/${project.id}?compose=1`)
  } catch (err) {
    startError.value = unwrapError(err)
  } finally {
    starting.value = false
  }
}

function openProject(id: string, focus: 'master' | 'wechat' = 'master'): void {
  router.push(`/projects/${id}?focus=${focus}`)
}

function onMetaEnter(event: KeyboardEvent): void {
  if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
    event.preventDefault()
    void startArticle('review')
  }
}
</script>

<template>
  <section class="home">
    <header class="intro">
      <h1>写一篇文章</h1>
      <p>写下主题和想法，一次生成带封面和插图的完整草稿。改动都可审阅，不会静默覆盖。</p>
    </header>

    <p v-if="error" class="banner bad">{{ error }}</p>
    <p v-if="startError" class="banner bad">{{ startError }}</p>

    <form class="compose" @submit.prevent="startArticle('review')">
      <label>
        主题
        <input
          v-model="topic"
          type="text"
          placeholder="例如：为什么测试全绿，我还是不敢用自己的产品"
          @keydown="onMetaEnter"
        />
      </label>
      <label>
        你的想法
        <textarea
          v-model="idea"
          rows="5"
          placeholder="这段话可以不完整。写下你真正想说的判断、经历或还没想清楚的问题。"
          @keydown="onMetaEnter"
        />
      </label>
      <label>
        <span class="label-row">资料 <em>可选</em></span>
        <textarea
          v-model="notes"
          rows="3"
          placeholder="粘贴笔记或摘录。今晚还不抓网页或 PDF。"
        />
      </label>
      <div class="actions">
        <button type="submit" class="primary" :disabled="!canStart || starting">
          {{ starting ? '正在开始…' : '生成文章' }}
        </button>
        <button type="button" class="ghost" :disabled="!canStart || starting" @click="startArticle('auto')">
          准备微信稿
        </button>
        <span>⌘ / Ctrl + Enter</span>
      </div>
    </form>

    <section v-if="latestProject || readyWechat" class="recent" aria-label="最近文章">
      <h2>最近</h2>
      <button
        v-if="readyWechat && readyWechat.id !== latestProject?.id"
        type="button"
        @click="openProject(readyWechat.id, 'wechat')"
      >
        <strong>{{ readyWechat.title }}</strong>
        <span>微信稿已就绪</span>
      </button>
      <button
        v-if="latestProject"
        type="button"
        @click="openProject(latestProject.id, latestHasWechat ? 'wechat' : 'master')"
      >
        <strong>{{ latestProject.title }}</strong>
        <span>{{ latestHasWechat ? '打开微信稿' : '打开文章' }} · {{ formatDateTime(latestProject.updated_at) }}</span>
      </button>
    </section>

    <p v-else-if="!loading" class="empty">最近写过的文章会出现在这里。</p>
  </section>
</template>

<style scoped>
.home {
  max-width: 680px;
  padding-top: 28px;
}

.intro h1 {
  margin: 0 0 10px;
  font-size: clamp(32px, 5vw, 48px);
  font-weight: 560;
  letter-spacing: -0.035em;
  line-height: 1.12;
  text-wrap: balance;
}

.intro p,
.empty {
  max-width: 42rem;
  margin: 0;
  color: var(--muted);
  line-height: 1.7;
}

.banner {
  margin: 18px 0 0;
  padding: 10px 12px;
  border-radius: var(--radius);
  line-height: 1.55;
}

.banner.bad {
  background: var(--bad-wash);
  color: var(--bad);
}

.compose {
  display: grid;
  gap: 22px;
  margin-top: 36px;
}

.compose label {
  display: grid;
  gap: 8px;
  color: var(--ink);
  font-size: 13px;
  font-weight: 560;
}

.compose .label-row {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.compose .label-row em {
  color: var(--faint);
  font-style: normal;
  font-weight: 400;
}

.compose input,
.compose textarea {
  width: 100%;
  padding: 12px 0;
  border: 0;
  border-bottom: 1px solid var(--line-strong);
  border-radius: 0;
  background: transparent;
  color: var(--ink);
  resize: vertical;
}

.compose input:focus,
.compose textarea:focus {
  outline: none;
  border-bottom-color: var(--ink);
}

.compose textarea {
  line-height: 1.7;
}

.actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  margin-top: 8px;
}

.primary,
.ghost {
  height: 40px;
  padding: 0 16px;
  border-radius: 6px;
  cursor: pointer;
  transition: background-color 200ms var(--ease), transform 200ms var(--ease), opacity 200ms var(--ease);
}

.primary {
  border: 0;
  background: var(--ink);
  color: var(--surface);
}

.ghost {
  border: 1px solid var(--line-strong);
  background: transparent;
  color: var(--ink);
}

.primary:hover,
.ghost:hover {
  opacity: 0.92;
}

.primary:active,
.ghost:active {
  transform: scale(0.98);
}

.primary:disabled,
.ghost:disabled {
  cursor: not-allowed;
  opacity: 0.4;
}

.actions span {
  color: var(--faint);
  font-size: 12px;
}

.recent {
  margin-top: 56px;
}

.recent h2 {
  margin: 0 0 8px;
  color: var(--faint);
  font-size: 12px;
  font-weight: 500;
}

.recent button {
  display: grid;
  width: 100%;
  gap: 4px;
  padding: 16px 0;
  border: 0;
  border-top: 1px solid var(--line);
  background: transparent;
  text-align: left;
  cursor: pointer;
}

.recent button:last-child {
  border-bottom: 1px solid var(--line);
}

.recent strong {
  font-size: 16px;
  font-weight: 560;
}

.recent span {
  color: var(--muted);
  font-size: 13px;
}

.recent button:hover strong {
  text-decoration: underline;
  text-underline-offset: 3px;
}

.empty {
  margin-top: 48px;
}

@media (max-width: 640px) {
  .home {
    padding-top: 8px;
  }
}
</style>
