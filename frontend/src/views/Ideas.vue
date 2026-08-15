<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import ComposeIntake, { type IntakeCommit, type IntakeSourceView } from '../components/ComposeIntake.vue'
import { useIdeasStore, useProjectsStore, useResearchStore, type IdeaItem } from '../stores'
import { unwrapError } from '../api/client'
import { formatDateTime } from '../utils/format'

const router = useRouter()
const store = useIdeasStore()
const projectsStore = useProjectsStore()
const researchStore = useResearchStore()
const { items, loading, error } = storeToRefs(store)
const saving = ref(false)
const saved = ref(false)
const starting = ref(false)
const formError = ref<string | null>(null)

const DEFAULTS = {
  audience: '27—39 岁左右、正在用 AI 重建工作方式的知识工作者',
  goal: '完成一篇可在微信公众号发布的图文草稿',
  voice: '第一人称、诚实克制、具体、不喊口号',
}

onMounted(() => store.load())

function kindLabel(idea: IdeaItem): string {
  if (idea.input_type === 'url') return idea.content.includes('mp.weixin.qq.com') ? '公众号' : '链接'
  return '想法'
}

function packedContent(idea: string, sources: IntakeSourceView[]): { input_type: IdeaItem['input_type']; content: string } {
  const links = sources.filter(item => item.reference.startsWith('http'))
  if (!idea && links.length === 1 && sources.length === 1) {
    return { input_type: 'url', content: links[0].reference }
  }
  const blocks = [idea]
  for (const source of sources) {
    const excerpt = source.excerpt.trim() || source.failure || ''
    blocks.push(`${source.reference}${excerpt ? `\n${excerpt.slice(0, 1600)}` : ''}`)
  }
  const content = blocks.filter(Boolean).join('\n\n')
  return { input_type: idea && !sources.length ? 'thought' : 'text', content }
}

async function saveIdea(payload: { idea: string; sources: IntakeSourceView[] }): Promise<void> {
  formError.value = null
  saving.value = true
  saved.value = false
  try {
    await store.create(packedContent(payload.idea, payload.sources))
    await store.load()
    saved.value = true
  } catch (err) {
    formError.value = unwrapError(err)
  } finally {
    saving.value = false
  }
}

async function writeNow(payload: IntakeCommit): Promise<void> {
  if (starting.value) return
  starting.value = true
  formError.value = null
  try {
    const packed = packedContent(payload.idea, payload.sources)
    await store.create(packed).catch(() => undefined)
    const idea = [payload.idea, ...payload.sources.map(source => {
      const excerpt = source.excerpt.trim() || source.failure || source.reference
      return `- [${source.kind}] ${source.title}\n  ${excerpt.slice(0, 1600)}`
    })].filter(Boolean).join('\n\n')
    const project = await projectsStore.create({
      title: payload.title,
      idea,
      ...DEFAULTS,
      autonomy: payload.mode === 'auto' ? 'pack' : 'draft',
    })
    for (const source of payload.sources) {
      const summary = (source.excerpt.trim() || source.failure || source.title).slice(0, 4000)
      try {
        await researchStore.addSource(project.id, {
          title: source.title,
          reference: source.reference,
          summary: summary || source.title,
        })
      } catch {
        // Idea text still carries the excerpt.
      }
    }
    await router.push(`/projects/${project.id}?compose=1`)
  } catch (err) {
    formError.value = unwrapError(err)
  } finally {
    starting.value = false
  }
}

function writeFromSaved(idea: IdeaItem): void {
  router.push({ path: '/', query: { seed: idea.id } })
}
</script>

<template>
  <section class="ideas-page">
    <header>
      <h1>灵感</h1>
      <p>先收下还没成形的一团东西。链接、文件、半句话都可以。写成文章后，再按正文挑标题。</p>
    </header>

    <p v-if="formError" class="banner bad">{{ formError }}</p>
    <p v-if="error" class="banner bad">{{ error }}</p>
    <p v-if="saved" class="banner">已记下这一条。</p>
    <p v-if="starting" class="banner">正在用你选的标题开始写…</p>

    <ComposeIntake show-save-action @save="saveIdea" @start="writeNow" />

    <div v-if="loading && !items.length" class="empty">正在读取…</div>
    <div v-else-if="items.length" class="idea-list">
      <article v-for="idea in items" :key="idea.id" class="idea-row">
        <div>
          <p class="kind">{{ kindLabel(idea) }}</p>
          <h2>{{ idea.title }}</h2>
          <p class="content">{{ idea.content }}</p>
          <time>{{ formatDateTime(idea.updated_at) }}</time>
        </div>
        <button
          v-if="!idea.project_id"
          type="button"
          class="ghost"
          @click="writeFromSaved(idea)"
        >
          写成文章
        </button>
        <button
          v-else
          type="button"
          class="text-btn"
          @click="router.push(`/projects/${idea.project_id}`)"
        >
          打开文章
        </button>
      </article>
    </div>
    <p v-else class="empty">这里还没有灵感。先丢一条观察、链接或文件进来。</p>
  </section>
</template>

<style scoped>
.ideas-page {
  max-width: 680px;
  padding-top: 28px;
}

.ideas-page > header {
  margin-bottom: 32px;
}

h1,
h2 {
  margin: 0;
  font-weight: 560;
  letter-spacing: -0.03em;
}

h1 {
  margin-bottom: 8px;
  font-size: clamp(28px, 4vw, 40px);
  line-height: 1.15;
}

.ideas-page > header > p,
.content,
.empty {
  color: var(--muted);
  line-height: 1.7;
}

.banner {
  margin: 0 0 18px;
  padding: 10px 12px;
  border-radius: var(--radius);
  line-height: 1.55;
}

.banner.bad {
  background: var(--bad-wash);
  color: var(--bad);
}

.idea-list {
  margin-top: 48px;
  border-top: 1px solid var(--line);
}

.idea-row {
  display: flex;
  justify-content: space-between;
  gap: 20px;
  padding: 20px 0;
  border-bottom: 1px solid var(--line);
}

.idea-row > div {
  min-width: 0;
}

.kind {
  margin: 0 0 6px;
  color: var(--faint);
  font-size: 12px;
}

.idea-row h2 {
  margin: 0 0 6px;
  font-size: 18px;
}

.content {
  max-width: 66ch;
  margin: 0 0 8px;
  overflow-wrap: anywhere;
}

time {
  color: var(--faint);
  font-size: 13px;
}

.ghost,
.text-btn {
  align-self: center;
  cursor: pointer;
}

.ghost {
  flex: 0 0 auto;
  height: 36px;
  padding: 0 12px;
  border: 1px solid var(--line-strong);
  border-radius: 6px;
  background: transparent;
  white-space: nowrap;
}

.text-btn {
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--ink);
  text-decoration: underline;
  text-underline-offset: 3px;
}

.empty {
  margin-top: 48px;
}

@media (max-width: 640px) {
  .ideas-page {
    padding-top: 8px;
  }

  .idea-row {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>
