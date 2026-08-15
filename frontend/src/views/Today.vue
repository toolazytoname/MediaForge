<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import ComposeIntake, { type IntakeCommit, type IntakeSourceView } from '../components/ComposeIntake.vue'
import { useIdeasStore, useProjectsStore, useResearchStore, useVariantsStore, type ProjectItem } from '../stores'
import { unwrapError } from '../api/client'
import { formatDateTime } from '../utils/format'

const route = useRoute()
const router = useRouter()
const projectsStore = useProjectsStore()
const ideasStore = useIdeasStore()
const researchStore = useResearchStore()
const variantsStore = useVariantsStore()
const { items, loading, error } = storeToRefs(projectsStore)
const latestProject = computed(() => items.value[0] ?? null)
const starting = ref(false)
const startError = ref<string | null>(null)
const latestHasWechat = ref(false)
const readyWechat = ref<ProjectItem | null>(null)
const seedIdea = ref('')
const seedSources = ref<IntakeSourceView[]>([])

const DEFAULTS = {
  audience: '27—39 岁左右、正在用 AI 重建工作方式的知识工作者',
  goal: '完成一篇可在微信公众号发布的图文草稿',
  voice: '第一人称、诚实克制、具体、不喊口号',
}

onMounted(async () => {
  await projectsStore.load()
  const seed = typeof route.query.seed === 'string' ? route.query.seed : ''
  if (seed) {
    await ideasStore.load()
    const idea = ideasStore.items.find(item => item.id === seed)
    if (idea) {
      if (idea.input_type === 'url') {
        seedSources.value = [{
          kind: idea.content.includes('mp.weixin.qq.com') ? 'wechat' : 'web',
          title: idea.title,
          reference: idea.content,
          excerpt: '',
          failure: null,
        }]
      } else {
        seedIdea.value = idea.content
      }
    }
  }
  for (const project of items.value.slice(0, 6)) {
    await variantsStore.load(project.id)
    const wechat = variantsStore.variants.find(item => item.platform === 'wechat_mp' && item.body.trim().length >= 600)
    if (wechat && !readyWechat.value) readyWechat.value = project
    if (latestProject.value && project.id === latestProject.value.id) {
      latestHasWechat.value = variantsStore.variants.some(item => item.platform === 'wechat_mp')
    }
  }
})

function ideaText(payload: IntakeCommit): string {
  const blocks = [payload.idea]
  if (payload.sources.length) {
    blocks.push('作者提供的资料：')
    for (const source of payload.sources) {
      const excerpt = source.excerpt.trim() || source.failure || source.reference
      blocks.push(`- [${source.kind}] ${source.title}\n  ${excerpt.slice(0, 1600)}`)
    }
  }
  return blocks.filter(Boolean).join('\n\n')
}

async function startArticle(payload: IntakeCommit): Promise<void> {
  if (starting.value) return
  starting.value = true
  startError.value = null
  try {
    const idea = ideaText(payload)
    const reusable = items.value.find(item => !item.has_master && item.title === payload.title)
    const project = reusable ?? await projectsStore.create({
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
        // Sources stay in the project idea text even if the research sidecar rejects one item.
      }
    }
    await router.push(payload.mode === 'auto' ? `/projects/${project.id}?compose=1&auto=1` : `/projects/${project.id}?compose=1`)
  } catch (err) {
    startError.value = unwrapError(err)
  } finally {
    starting.value = false
  }
}

function openProject(id: string, focus: 'master' | 'wechat' = 'master'): void {
  router.push(`/projects/${id}?focus=${focus}`)
}
</script>

<template>
  <section class="home">
    <header class="intro">
      <h1>写一篇文章</h1>
      <p>不用先起标题。把想法、链接或文件丢进来，先挑一个标题，再生成带封面和插图的草稿。</p>
    </header>

    <p v-if="error" class="banner bad">{{ error }}</p>
    <p v-if="startError" class="banner bad">{{ startError }}</p>
    <p v-if="starting" class="banner">正在用你选的标题开始写…</p>

    <ComposeIntake
      :initial-idea="seedIdea"
      :initial-sources="seedSources"
      show-auto-action
      @start="startArticle"
    />

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

.intro {
  margin-bottom: 32px;
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
  margin: 0 0 18px;
  padding: 10px 12px;
  border-radius: var(--radius);
  line-height: 1.55;
}

.banner.bad {
  background: var(--bad-wash);
  color: var(--bad);
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
