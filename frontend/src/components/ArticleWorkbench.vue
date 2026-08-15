<script setup lang="ts">
import { computed, ref } from 'vue'
import { renderMarkdown } from '../utils/markdown'
import type { MasterSuggestion } from '../stores'

const props = defineProps<{
  title: string
  body: string
  suggestions: MasterSuggestion[]
  suggesting: boolean
  saving: boolean
  error: string | null
  titleOptions?: string[]
  titlesLoading?: boolean
}>()

const emit = defineEmits<{
  'update:title': [value: string]
  'update:body': [value: string]
  save: []
  request: [input: { action: MasterSuggestion['action']; selection: string | null; note?: string }]
  accept: [suggestion: MasterSuggestion]
  reject: [suggestion: MasterSuggestion]
  'request-titles': []
  'apply-title': [title: string]
}>()

const customTitle = ref('')
const titlesOpen = ref(false)

type ArticleBlock = { id: string; text: string; kind: 'heading' | 'paragraph' | 'image' }

const selectedId = ref<string | null>(null)
const customNote = ref('')
const wholeNote = ref('')
const wholeOpen = ref(false)
const showSource = ref(false)

const blocks = computed(() => {
  const all = splitBlocks(props.body)
  if (all[0]?.kind === 'heading' && all[0].text.replace(/^#\s+/, '').trim() === props.title.trim()) {
    return all.slice(1)
  }
  return all
})
const pending = computed(() => props.suggestions.filter(item => item.status === 'pending'))
const selected = computed(() => blocks.value.find(item => item.id === selectedId.value) ?? null)

function splitBlocks(body: string): ArticleBlock[] {
  return body.split(/\n{2,}/).map((text, index) => {
    const trimmed = text.trim()
    const kind = trimmed.startsWith('![') ? 'image' as const : trimmed.startsWith('#') ? 'heading' as const : 'paragraph' as const
    return { id: `blk_${index}`, text: trimmed, kind }
  }).filter(item => item.text)
}

function pendingFor(block: ArticleBlock): MasterSuggestion | undefined {
  return pending.value.find(item => item.selection === block.text)
}

function wholePending(): MasterSuggestion | undefined {
  return pending.value.find(item => item.selection == null)
}

function replacementText(suggestion: MasterSuggestion): string {
  if (!suggestion.selection) return suggestion.proposed_body
  const parts = props.body.split(suggestion.selection)
  if (parts.length !== 2) return suggestion.proposed_body
  const [before, after] = parts
  if (suggestion.proposed_body.startsWith(before) && suggestion.proposed_body.endsWith(after)) {
    return suggestion.proposed_body.slice(before.length, suggestion.proposed_body.length - after.length)
  }
  return suggestion.proposed_body
}

function selectBlock(block: ArticleBlock): void {
  if (block.kind === 'image') return
  selectedId.value = selectedId.value === block.id ? null : block.id
  customNote.value = ''
}

function requestBlock(action: MasterSuggestion['action'], note?: string): void {
  if (!selected.value || props.suggesting) return
  emit('request', { action, selection: selected.value.text, note: note?.trim() || undefined })
}

function requestWhole(): void {
  if (props.suggesting) return
  emit('request', { action: 'clarify', selection: null, note: wholeNote.value.trim() || undefined })
}

const actionLabel: Record<MasterSuggestion['action'], string> = {
  clarify: '改清楚',
  shorten: '写短些',
  change_voice: '换口吻',
  add_counterpoint: '补反方',
}

const imageCount = computed(() => blocks.value.filter(item => item.kind === 'image').length)

function requestTitles(): void {
  titlesOpen.value = true
  emit('request-titles')
}
</script>

<template>
  <section class="workbench">
    <header class="toolbar">
      <p>{{ imageCount ? `${imageCount} 张图 · 点一段文字可改` : '点一段文字可改 · 封面和插图还没补上' }}</p>
      <div class="toolbar-actions">
        <button type="button" class="text" :class="{ on: titlesOpen }" :disabled="!body.trim()" @click="titlesOpen ? titlesOpen = false : requestTitles()">
          {{ titlesLoading ? '正在起标题…' : '换标题' }}
        </button>
        <button type="button" class="text" :class="{ on: wholeOpen }" @click="wholeOpen = !wholeOpen">整篇意见</button>
        <button type="button" class="text" @click="showSource = !showSource">{{ showSource ? '看排版' : '改原文' }}</button>
        <button type="button" class="primary" :disabled="saving" @click="emit('save')">{{ saving ? '保存中' : '保存' }}</button>
      </div>
    </header>

    <p v-if="error" class="banner">{{ error }}</p>

    <div v-if="titlesOpen" class="title-picker">
      <header>
        <p>按这篇正文起标题</p>
        <button type="button" class="text" :disabled="titlesLoading || !body.trim()" @click="emit('request-titles')">
          {{ titlesLoading ? '正在起标题…' : titleOptions?.length ? '换一批' : '起几个标题' }}
        </button>
      </header>
      <div v-if="titleOptions?.length" class="title-options">
        <button
          v-for="option in titleOptions"
          :key="option"
          type="button"
          :class="{ on: title === option }"
          @click="emit('apply-title', option)"
        >
          {{ option }}
        </button>
        <label>
          <input
            v-model="customTitle"
            type="text"
            placeholder="自己写一个"
            @keydown.enter="customTitle.trim() && emit('apply-title', customTitle.trim())"
          />
          <button type="button" :disabled="!customTitle.trim()" @click="emit('apply-title', customTitle.trim())">用这个</button>
        </label>
      </div>
    </div>

    <div v-if="wholeOpen" class="whole-box">
      <label>
        对整篇说一句
        <textarea v-model="wholeNote" rows="3" placeholder="例如：少说教，保留真实失败；开头再具体一点。" />
      </label>
      <button type="button" class="primary" :disabled="suggesting || !body.trim()" @click="requestWhole">
        {{ suggesting ? '生成中…' : '生成整篇建议' }}
      </button>
      <article v-if="wholePending()" class="compare whole">
        <div>
          <strong>现在</strong>
          <pre>{{ body.slice(0, 600) }}{{ body.length > 600 ? '…' : '' }}</pre>
        </div>
        <div>
          <strong>建议</strong>
          <pre>{{ replacementText(wholePending()!).slice(0, 600) }}{{ replacementText(wholePending()!).length > 600 ? '…' : '' }}</pre>
        </div>
        <div class="compare-actions">
          <button type="button" class="primary" @click="emit('accept', wholePending()!)">采用整篇建议</button>
          <button type="button" class="ghost" @click="emit('reject', wholePending()!)">不用</button>
        </div>
      </article>
    </div>

    <div v-if="showSource" class="source">
      <input :value="title" placeholder="标题" @input="emit('update:title', ($event.target as HTMLInputElement).value)" />
      <textarea :value="body" rows="16" placeholder="正文" @input="emit('update:body', ($event.target as HTMLTextAreaElement).value)" />
    </div>

    <article v-else class="article">
      <h1>{{ title }}</h1>
      <p v-if="!imageCount" class="missing-art">封面和插图还没有。出图接口失败时，正文会先出来；图可以稍后补上。</p>
      <section
        v-for="block in blocks"
        :key="block.id"
        :class="['block', block.kind, { active: selectedId === block.id, pending: Boolean(pendingFor(block)) }]"
      >
        <div class="block-body" v-html="renderMarkdown(block.text)" @click="selectBlock(block)" />
        <div v-if="selectedId === block.id && block.kind !== 'image'" class="inline-bar">
          <span>改这一段</span>
          <button type="button" :disabled="suggesting" @click="requestBlock('clarify')">改清楚</button>
          <button type="button" :disabled="suggesting" @click="requestBlock('shorten')">写短些</button>
          <button type="button" :disabled="suggesting" @click="requestBlock('change_voice')">换口吻</button>
          <input
            v-model="customNote"
            type="text"
            placeholder="或写一句：少说教 / 更具体"
            @keydown.enter="requestBlock('clarify', customNote)"
          />
          <button type="button" class="primary" :disabled="suggesting || !customNote.trim()" @click="requestBlock('clarify', customNote)">
            按这句话改
          </button>
        </div>
        <div v-if="pendingFor(block)" class="compare">
          <div>
            <strong>现在</strong>
            <pre>{{ pendingFor(block)!.selection }}</pre>
          </div>
          <div>
            <strong>建议 · {{ actionLabel[pendingFor(block)!.action] }}</strong>
            <pre>{{ replacementText(pendingFor(block)!) }}</pre>
          </div>
          <div class="compare-actions">
            <button type="button" class="primary" aria-label="采用这段建议" @click="emit('accept', pendingFor(block)!)">采用</button>
            <button type="button" class="ghost" aria-label="不用这段建议" @click="emit('reject', pendingFor(block)!)">不用</button>
          </div>
        </div>
      </section>
      <p v-if="!blocks.length" class="empty">还没有正文。</p>
    </article>
  </section>
</template>

<style scoped>
.workbench {
  display: grid;
  gap: 16px;
}

.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--line);
}

.toolbar p {
  margin: 0;
  color: var(--muted);
  font-size: 13px;
}

.toolbar-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.banner {
  margin: 0;
  padding: 10px 12px;
  border-radius: var(--radius);
  background: var(--bad-wash);
  color: var(--bad);
}

.whole-box,
.source {
  display: grid;
  gap: 10px;
  padding: 14px 0;
}

.whole-box label {
  display: grid;
  gap: 6px;
  color: var(--ink);
  font-size: 13px;
  font-weight: 560;
}

.source input,
.source textarea,
.whole-box textarea,
.inline-bar input {
  width: 100%;
  padding: 10px 0;
  border: 0;
  border-bottom: 1px solid var(--line-strong);
  background: transparent;
  color: var(--ink);
}

.source input {
  font-family: var(--font-read);
  font-size: 28px;
  letter-spacing: -0.03em;
}

.source textarea,
.whole-box textarea {
  line-height: 1.75;
  resize: vertical;
}

.article {
  max-width: 42rem;
  margin: 0 auto;
  padding: 8px 0 48px;
}

.article h1 {
  margin: 8px 0 18px;
  color: var(--ink);
  font-family: var(--font-read);
  font-size: clamp(28px, 4vw, 40px);
  font-weight: 620;
  letter-spacing: -0.035em;
  line-height: 1.22;
  text-wrap: balance;
}

.missing-art {
  margin: 0 0 20px;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.6;
}

.title-picker {
  margin: 0 0 28px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--line);
}

.title-picker header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 12px;
}

.title-picker p {
  margin: 0;
  color: var(--faint);
  font-size: 12px;
}

.title-options {
  display: grid;
  gap: 8px;
  margin-top: 12px;
}

.title-options > button {
  padding: 10px 0;
  border: 0;
  border-top: 1px solid var(--line);
  background: transparent;
  text-align: left;
  cursor: pointer;
}

.title-options > button.on {
  font-weight: 560;
}

.title-options label {
  display: flex;
  gap: 10px;
  align-items: center;
}

.title-options input {
  flex: 1;
  min-width: 0;
  padding: 8px 0;
  border: 0;
  border-bottom: 1px solid var(--line-strong);
  background: transparent;
}

.block {
  margin: 0 0 4px;
}

.block-body {
  padding: 8px 0;
  cursor: pointer;
  border-left: 2px solid transparent;
}

.block-body :deep(p),
.block-body :deep(h2),
.block-body :deep(li) {
  margin: 0;
  line-height: 1.85;
}

.block-body :deep(h2) {
  font-family: var(--font-read);
  font-size: 22px;
  font-weight: 600;
}

.block-body :deep(img) {
  display: block;
  width: 100%;
  height: auto;
  border-radius: 4px;
}

.block.image .block-body {
  cursor: default;
  padding: 8px 0 20px;
}

.block.image:first-of-type .block-body :deep(img) {
  border-radius: 8px;
}

.block.active .block-body,
.block-body:hover {
  border-left-color: var(--ink);
  padding-left: 10px;
}

.block.pending .block-body {
  border-left-color: var(--warn);
}

.inline-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin: 4px 0 16px;
}

.inline-bar span {
  color: var(--muted);
  font-size: 12px;
}

.inline-bar input {
  max-width: 240px;
  padding: 6px 0;
}

.inline-bar button,
.text,
.primary,
.ghost {
  height: 32px;
  padding: 0 10px;
  border-radius: 6px;
  cursor: pointer;
  transition: background-color 200ms var(--ease), transform 200ms var(--ease), opacity 200ms var(--ease);
}

.inline-bar button,
.ghost,
.text {
  border: 1px solid var(--line-strong);
  background: transparent;
  color: var(--ink);
}

.text {
  border-color: transparent;
  color: var(--muted);
}

.text.on,
.text:hover {
  color: var(--ink);
  background: var(--wash);
}

.primary {
  border: 0;
  background: var(--ink);
  color: var(--surface);
}

.primary:disabled,
.inline-bar button:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.primary:active,
.ghost:active,
.inline-bar button:active {
  transform: scale(0.98);
}

.compare {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  margin: 8px 0 18px;
  padding: 14px 0;
  border-top: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
}

.compare strong {
  color: var(--muted);
  font-size: 12px;
  font-weight: 500;
}

.compare pre {
  margin: 8px 0 0;
  max-height: 240px;
  overflow: auto;
  white-space: pre-wrap;
  color: var(--ink);
  font-family: inherit;
  font-size: 14px;
  line-height: 1.7;
}

.compare-actions {
  grid-column: 1 / -1;
  display: flex;
  gap: 8px;
}

.empty {
  color: var(--faint);
}

@media (max-width: 760px) {
  .toolbar,
  .compare {
    display: grid;
    grid-template-columns: 1fr;
  }
}
</style>
