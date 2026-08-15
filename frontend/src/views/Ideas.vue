<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import { BulbOutlined, PlusOutlined } from '@ant-design/icons-vue'
import { useIdeasStore, type IdeaItem } from '../stores'
import { unwrapError } from '../api/client'
import { formatDateTime } from '../utils/format'

const router = useRouter()
const store = useIdeasStore()
const { items, loading, error } = storeToRefs(store)
const saving = ref(false)
const formError = ref<string | null>(null)
const form = reactive<{ input_type: IdeaItem['input_type']; title: string; content: string }>({
  input_type: 'thought', title: '', content: '',
})
const placeholder = computed(() => form.input_type === 'url' ? 'https://...' : form.input_type === 'text' ? '粘贴一段笔记、摘录或材料' : '写下一个观察、问题或还没想完整的观点')

onMounted(() => store.load())

async function saveIdea(): Promise<void> {
  formError.value = null
  saving.value = true
  try {
    await store.create({ ...form })
    form.title = ''
    form.content = ''
    await store.load()
  } catch (error) {
    formError.value = unwrapError(error)
  } finally {
    saving.value = false
  }
}

function createProject(idea: IdeaItem): void {
  router.push({ path: '/projects/new', query: { ideaId: idea.id } })
}
</script>

<template>
  <section class="ideas-page">
    <header><h1>灵感</h1><p>先收下还没成形的观察、链接和材料。准备好了，再把它写成文章。</p></header>
    <a-card :bordered="false" class="capture-card">
      <a-form layout="vertical" @finish="saveIdea">
        <a-form-item label="这是什么材料？"><a-radio-group v-model:value="form.input_type"><a-radio-button value="thought">一句想法</a-radio-button><a-radio-button value="url">URL</a-radio-button><a-radio-button value="text">粘贴文本</a-radio-button></a-radio-group></a-form-item>
        <a-form-item label="给它一个便于寻找的标题" required><a-input v-model:value="form.title" placeholder="例如：创作工具不该先教人跑流程" /></a-form-item>
        <a-form-item :label="form.input_type === 'url' ? '链接' : '内容'" required><a-textarea v-model:value="form.content" :placeholder="placeholder" :auto-size="{ minRows: 3, maxRows: 8 }" /></a-form-item>
        <a-alert v-if="formError" type="error" :message="formError" show-icon class="form-error" />
        <a-button type="primary" html-type="button" :loading="saving" @click="saveIdea"><PlusOutlined /> 保存灵感</a-button>
      </a-form>
    </a-card>
    <a-alert v-if="error" type="error" :message="error" show-icon class="notice" />
    <a-spin :spinning="loading"><div v-if="items.length" class="idea-list"><article v-for="idea in items" :key="idea.id" class="idea-row"><div><p class="kind">{{ idea.input_type === 'thought' ? '一句想法' : idea.input_type === 'url' ? 'URL' : '粘贴文本' }}</p><h2>{{ idea.title }}</h2><p class="content">{{ idea.content }}</p><time>{{ formatDateTime(idea.updated_at) }}</time></div><a-button v-if="!idea.project_id" @click="createProject(idea)">发展成项目</a-button><a-button v-else type="link" @click="router.push(`/projects/${idea.project_id}`)">打开项目</a-button></article></div><a-empty v-else-if="!loading" description="这里还没有灵感。先保存一个真实观察。"><template #image><BulbOutlined class="empty-icon" /></template></a-empty></a-spin>
  </section>
</template>

<style scoped>
.ideas-page { max-width: 720px; padding-top: 8px; }
.ideas-page > header { margin-bottom: 24px; }
.kind { margin: 0 0 6px; color: var(--faint); font-size: 12px; }
h1, h2 { margin: 0; font-weight: 560; letter-spacing: -0.03em; }
h1 { margin-bottom: 8px; font-size: clamp(28px, 4vw, 40px); line-height: 1.15; }
.ideas-page > header > p, .content { color: var(--muted); line-height: 1.7; }
.capture-card { margin-bottom: 28px; border: 1px solid var(--line); background: var(--surface); box-shadow: none; }
.form-error, .notice { margin-bottom: 14px; }
.idea-list { border-top: 1px solid var(--line); }
.idea-row { display: flex; justify-content: space-between; gap: 20px; padding: 20px 0; border-bottom: 1px solid var(--line); }
.idea-row > div { min-width: 0; }
.idea-row h2 { margin: 0 0 6px; font-size: 18px; }
.content { max-width: 66ch; margin: 0 0 8px; overflow-wrap: anywhere; }
time { color: var(--faint); font-size: 13px; }
.empty-icon { color: var(--faint); font-size: 32px; }
@media (max-width: 640px) { .idea-row { align-items: flex-start; flex-direction: column; } }
</style>
