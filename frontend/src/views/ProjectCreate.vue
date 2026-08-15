<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useIdeasStore, useProjectsStore, type ProjectInput } from '../stores'
import { unwrapError } from '../api/client'

const route = useRoute()
const router = useRouter()
const projects = useProjectsStore()
const ideas = useIdeasStore()
const saving = ref(false)
const pageError = ref<string | null>(null)
const ideaId = computed(() => typeof route.query.ideaId === 'string' ? route.query.ideaId : null)
const sourceIdea = computed(() => ideaId.value ? ideas.items.find((item) => item.id === ideaId.value) ?? null : null)
const form = reactive<ProjectInput>({ title: '', idea: '', audience: '', goal: '', voice: '', autonomy: 'collaborate' })

onMounted(async () => {
  if (ideaId.value) {
    await ideas.load()
    if (sourceIdea.value) {
      form.title = sourceIdea.value.title
      form.idea = sourceIdea.value.content
    } else {
      pageError.value = '没有找到这条灵感。你仍可以直接创建一个新项目。'
    }
  }
})

async function createProject(): Promise<void> {
  pageError.value = null
  saving.value = true
  try {
    const project = sourceIdea.value
      ? (await ideas.promote(sourceIdea.value.id, { title: form.title, audience: form.audience, goal: form.goal, voice: form.voice, autonomy: form.autonomy })).project
      : await projects.create(form)
    router.push(`/projects/${project.id}`)
  } catch (error) {
    pageError.value = unwrapError(error)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <section class="create-page">
    <button type="button" class="back" @click="router.push(ideaId ? '/ideas' : '/projects')">返回</button>
    <header><h1>{{ sourceIdea ? '把这条灵感写成文章' : '新文章' }}</h1><p>先确定写给谁、这次要完成什么。还不绑定平台。</p></header>
    <a-card :bordered="false" class="form-card"><a-form layout="vertical" @finish="createProject">
      <a-form-item label="项目标题" required><a-input v-model:value="form.title" placeholder="给这次创作一个清晰的名字" /></a-form-item>
      <a-form-item label="核心想法或材料" required><a-textarea v-model:value="form.idea" :auto-size="{ minRows: 3, maxRows: 8 }" placeholder="一句想法、链接，或一段你已经写下来的材料" /></a-form-item>
      <div class="form-grid"><a-form-item label="写给谁" required><a-input v-model:value="form.audience" placeholder="例如：正在建立个人品牌的独立创作者" /></a-form-item><a-form-item label="这次想完成什么" required><a-input v-model:value="form.goal" placeholder="例如：完成一篇有依据的主稿" /></a-form-item></div>
      <a-form-item label="声音" required><a-input v-model:value="form.voice" placeholder="例如：清楚、克制、有个人判断" /></a-form-item>
      <a-form-item label="这次 AI 参与到什么程度？" required><a-radio-group v-model:value="form.autonomy"><a-radio value="assist">我写，AI 协助</a-radio><a-radio value="collaborate">一起写</a-radio><a-radio value="draft">AI 先起草</a-radio><a-radio value="pack">AI 准备内容包</a-radio></a-radio-group></a-form-item>
      <a-alert v-if="pageError" type="error" :message="pageError" show-icon class="form-error" />
      <a-button type="primary" html-type="button" :loading="saving" @click="createProject">创建项目并进入工作台</a-button>
    </a-form></a-card>
  </section>
</template>

<style scoped>
.create-page { max-width: 680px; padding-top: 4px; }
.back { margin-bottom: 18px; padding: 0; border: 0; background: transparent; color: var(--muted); cursor: pointer; }
.back:hover { color: var(--ink); }
.create-page header { margin-bottom: 24px; }
h1 { margin: 0 0 8px; font-size: clamp(28px, 4vw, 40px); font-weight: 560; letter-spacing: -0.03em; line-height: 1.15; }
.create-page header > p { margin: 0; color: var(--muted); line-height: 1.7; }
.form-card { border: 1px solid var(--line); background: var(--surface); box-shadow: none; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.form-error { margin-bottom: 16px; }
@media (max-width: 640px) { .form-grid { grid-template-columns: 1fr; gap: 0; } }
</style>
