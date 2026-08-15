<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { api, unwrapError, GENERATION_TIMEOUT_MS } from '../api/client'

export type IntakeKind = 'wechat' | 'web' | 'markdown' | 'pdf' | 'text' | 'file'

export interface IntakeSourceView {
  kind: IntakeKind
  title: string
  reference: string
  excerpt: string
  failure: string | null
  file?: File
}

export interface IntakeCommit {
  title: string
  idea: string
  sources: IntakeSourceView[]
  mode: 'review' | 'auto'
}

const props = withDefaults(defineProps<{
  initialIdea?: string
  initialSources?: IntakeSourceView[]
  showAutoAction?: boolean
  showSaveAction?: boolean
}>(), {
  initialIdea: '',
  initialSources: () => [],
  showAutoAction: false,
  showSaveAction: false,
})

const emit = defineEmits<{
  start: [payload: IntakeCommit]
  save: [payload: { idea: string; sources: IntakeSourceView[] }]
}>()

const idea = ref(props.initialIdea)
const sources = ref<IntakeSourceView[]>([...props.initialSources])
const linkOpen = ref(false)
const urlDraft = ref('')
const fileInput = ref<HTMLInputElement | null>(null)
const preparing = ref(false)
const prepareError = ref<string | null>(null)

const canPrepare = computed(() => Boolean(idea.value.trim() || sources.value.length))

watch(() => props.initialIdea, value => {
  if (value && !idea.value) idea.value = value
})

watch(() => props.initialSources, value => {
  if (value.length && !sources.value.length) sources.value = [...value]
})

function kindLabel(kind: IntakeKind): string {
  if (kind === 'wechat') return '公众号'
  if (kind === 'web') return '网页'
  if (kind === 'markdown') return 'Markdown'
  if (kind === 'pdf') return 'PDF'
  if (kind === 'text') return '文本'
  return '文件'
}

function classifyUrl(url: string): IntakeKind {
  try {
    const host = new URL(url).hostname
    return host === 'mp.weixin.qq.com' || host.endsWith('.mp.weixin.qq.com') ? 'wechat' : 'web'
  } catch {
    return 'web'
  }
}

function addUrl(): void {
  const value = urlDraft.value.trim()
  if (!value) return
  let parsed: URL
  try {
    parsed = new URL(value)
  } catch {
    prepareError.value = '请输入完整链接，例如 https://mp.weixin.qq.com/s/...'
    return
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
    prepareError.value = '只接受 http 或 https 链接'
    return
  }
  if (sources.value.some(item => item.reference === parsed.toString())) {
    urlDraft.value = ''
    linkOpen.value = false
    return
  }
  sources.value = [...sources.value, {
    kind: classifyUrl(parsed.toString()),
    title: parsed.hostname.replace(/^www\./, ''),
    reference: parsed.toString(),
    excerpt: '',
    failure: null,
  }]
  urlDraft.value = ''
  linkOpen.value = false
  prepareError.value = null
}

function onFiles(event: Event): void {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files ?? [])
  input.value = ''
  for (const file of files) {
    const ext = file.name.split('.').pop()?.toLowerCase()
    const kind: IntakeKind = ext === 'pdf' ? 'pdf' : ext === 'md' || ext === 'markdown' ? 'markdown' : ext === 'txt' ? 'text' : 'file'
    if (kind === 'file') {
      prepareError.value = '目前只收 Markdown、PDF 或纯文本'
      continue
    }
    if (sources.value.some(item => item.file?.name === file.name || item.reference === `local:${file.name}`)) continue
    sources.value = [...sources.value, {
      kind,
      title: file.name,
      reference: `local:${file.name}`,
      excerpt: '',
      failure: null,
      file,
    }]
  }
}

function removeSource(reference: string): void {
  sources.value = sources.value.filter(item => item.reference !== reference)
}

function onMetaEnter(event: KeyboardEvent): void {
  if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
    event.preventDefault()
    void prepare('review')
  }
}

function workingTitle(): string {
  const line = idea.value.trim().split('\n').find(item => item.trim()) ?? ''
  return line.slice(0, 36) || sources.value[0]?.title || '未命名文章'
}

async function prepare(mode: 'review' | 'auto'): Promise<void> {
  if (!canPrepare.value || preparing.value) return
  preparing.value = true
  prepareError.value = null
  try {
    const form = new FormData()
    form.append('idea', idea.value)
    for (const source of sources.value) {
      if (source.file) form.append('files', source.file, source.file.name)
      else if (source.reference.startsWith('http')) form.append('urls', source.reference)
    }
    const response = await api.post<{ idea: string; sources: IntakeSourceView[] }>(
      '/intake/prepare',
      form,
      { timeout: GENERATION_TIMEOUT_MS },
    )
    idea.value = response.data.idea || idea.value
    const extracted = response.data.sources.map(item => {
      const previous = sources.value.find(source => source.reference === item.reference)
      return { ...item, file: previous?.file }
    })
    sources.value = extracted
    emit('start', {
      title: workingTitle(),
      idea: idea.value.trim(),
      sources: sources.value,
      mode,
    })
  } catch (error) {
    prepareError.value = unwrapError(error)
  } finally {
    preparing.value = false
  }
}

function save(): void {
  if (!canPrepare.value) return
  emit('save', { idea: idea.value.trim(), sources: sources.value })
}
</script>

<template>
  <form class="intake" @submit.prevent="prepare('review')">
    <label class="dump">
      <span>想法和资料</span>
      <textarea
        v-model="idea"
        rows="8"
        placeholder="不用起标题。把脑子里的判断、经历、问题丢进来。一篇公众号、一个网页、一段摘录，都可以。标题等正文出来再选。"
        @keydown="onMetaEnter"
      />
    </label>

    <div class="attach">
      <button type="button" class="text-btn" @click="linkOpen = !linkOpen">添加链接</button>
      <button type="button" class="text-btn" @click="fileInput?.click()">添加文件</button>
      <span>公众号、网页、Markdown、PDF</span>
      <input
        ref="fileInput"
        type="file"
        accept=".md,.markdown,.txt,.pdf,text/markdown,text/plain,application/pdf"
        multiple
        hidden
        @change="onFiles"
      />
    </div>

    <div v-if="linkOpen" class="link-row">
      <input
        v-model="urlDraft"
        type="url"
        placeholder="https://mp.weixin.qq.com/s/… 或任意网页"
        @keydown.enter.prevent="addUrl"
      />
      <button type="button" class="ghost" @click="addUrl">加入</button>
    </div>

    <ul v-if="sources.length" class="chips">
      <li v-for="source in sources" :key="source.reference">
        <em>{{ kindLabel(source.kind) }}</em>
        <span>{{ source.title }}</span>
        <small v-if="source.failure">{{ source.failure }}</small>
        <button type="button" aria-label="去掉这份资料" @click="removeSource(source.reference)">×</button>
      </li>
    </ul>

    <p v-if="prepareError" class="banner bad">{{ prepareError }}</p>

    <div class="actions">
      <button type="submit" class="primary" :disabled="!canPrepare || preparing">
        {{ preparing ? '正在读资料并开始写…' : '生成文章' }}
      </button>
      <button
        v-if="showAutoAction"
        type="button"
        class="ghost"
        :disabled="!canPrepare || preparing"
        @click="prepare('auto')"
      >
        准备微信稿
      </button>
      <button
        v-if="showSaveAction"
        type="button"
        class="ghost"
        :disabled="!canPrepare || preparing"
        @click="save"
      >
        先收下
      </button>
      <span>⌘ / Ctrl + Enter</span>
    </div>
  </form>
</template>

<style scoped>
.intake {
  display: grid;
  gap: 18px;
}

.dump {
  display: grid;
  gap: 8px;
  color: var(--ink);
  font-size: 13px;
  font-weight: 560;
}

.dump textarea {
  width: 100%;
  min-height: 180px;
  padding: 12px 0;
  border: 0;
  border-bottom: 1px solid var(--line-strong);
  background: transparent;
  color: var(--ink);
  line-height: 1.7;
  resize: vertical;
}

.dump textarea:focus {
  outline: none;
  border-bottom-color: var(--ink);
}

.attach,
.link-row,
.actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
}

.attach span {
  color: var(--faint);
  font-size: 12px;
}

.text-btn {
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--ink);
  text-decoration: underline;
  text-underline-offset: 3px;
  cursor: pointer;
}

.link-row input,
.titles input[type="text"] {
  flex: 1;
  min-width: 0;
  padding: 10px 0;
  border: 0;
  border-bottom: 1px solid var(--line-strong);
  background: transparent;
}

.link-row input:focus,
.titles input[type="text"]:focus {
  outline: none;
  border-bottom-color: var(--ink);
}

.chips {
  display: grid;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.chips li {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 8px;
  padding: 8px 0;
  border-top: 1px solid var(--line);
}

.chips em {
  color: var(--faint);
  font-size: 12px;
  font-style: normal;
}

.chips span {
  flex: 1;
}

.chips small {
  flex-basis: 100%;
  color: var(--bad);
}

.chips button {
  border: 0;
  background: transparent;
  color: var(--muted);
  cursor: pointer;
}

.banner {
  margin: 0;
  padding: 10px 12px;
  border-radius: var(--radius);
  line-height: 1.55;
}

.banner.bad {
  background: var(--bad-wash);
  color: var(--bad);
}

.titles {
  display: grid;
  gap: 10px;
  padding-top: 8px;
}

.titles header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 12px;
}

.titles h2 {
  margin: 0;
  font-size: 16px;
  font-weight: 560;
}

.titles > p {
  margin: 0;
  color: var(--muted);
  font-size: 13px;
}

.titles input[type="radio"] {
  accent-color: var(--ink);
}

.titles label {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 0;
  border-top: 1px solid var(--line);
  cursor: pointer;
}

.titles label.on span {
  font-weight: 560;
}

.primary,
.ghost {
  height: 40px;
  padding: 0 16px;
  border-radius: 6px;
  cursor: pointer;
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

.primary:disabled,
.ghost:disabled {
  cursor: not-allowed;
  opacity: 0.4;
}

.actions span {
  color: var(--faint);
  font-size: 12px;
}
</style>
