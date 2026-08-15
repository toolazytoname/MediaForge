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
}>()

const emit = defineEmits<{
  'update:title': [value: string]
  'update:body': [value: string]
  save: []
  request: [input: { action: MasterSuggestion['action']; selection: string | null; note?: string }]
  accept: [suggestion: MasterSuggestion]
  reject: [suggestion: MasterSuggestion]
}>()

type ArticleBlock = { id: string; text: string; kind: 'heading' | 'paragraph' | 'image' }

const selectedId = ref<string | null>(null)
const customNote = ref('')
const wholeNote = ref('')
const wholeOpen = ref(false)
const showSource = ref(false)

const blocks = computed(() => splitBlocks(props.body))
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
</script>

<template>
  <section class="workbench">
    <header class="toolbar">
      <div>
        <p class="hint">点一段文字，就地改。整篇意见在右边。</p>
      </div>
      <div class="toolbar-actions">
        <a-button @click="showSource = !showSource">{{ showSource ? '看文章' : '改原文' }}</a-button>
        <a-button type="primary" :loading="saving" @click="emit('save')">保存</a-button>
        <a-button :type="wholeOpen ? 'primary' : 'default'" @click="wholeOpen = !wholeOpen">整篇意见</a-button>
      </div>
    </header>

    <a-alert v-if="error" type="error" :message="error" show-icon class="notice" />

    <div v-if="wholeOpen" class="whole-box">
      <label>
        对整篇说一句
        <a-textarea v-model:value="wholeNote" :auto-size="{ minRows: 2, maxRows: 5 }" placeholder="例如：少说教，保留真实失败；开头再具体一点。" />
      </label>
      <a-button type="primary" :loading="suggesting" :disabled="!body.trim()" @click="requestWhole">生成整篇建议</a-button>
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
          <a-button type="primary" @click="emit('accept', wholePending()!)">采用整篇建议</a-button>
          <a-button @click="emit('reject', wholePending()!)">不用</a-button>
        </div>
      </article>
    </div>

    <div v-if="showSource" class="source">
      <a-input :value="title" placeholder="标题" @update:value="emit('update:title', $event)" />
      <a-textarea :value="body" :auto-size="{ minRows: 12, maxRows: 28 }" placeholder="正文" @update:value="emit('update:body', $event)" />
    </div>

    <article v-else class="article">
      <h1 v-if="!body.trimStart().startsWith('# ')">{{ title }}</h1>
      <section
        v-for="block in blocks"
        :key="block.id"
        :class="['block', block.kind, { active: selectedId === block.id, pending: Boolean(pendingFor(block)) }]"
      >
        <div class="block-body" v-html="renderMarkdown(block.text)" @click="selectBlock(block)" />
        <div v-if="selectedId === block.id && block.kind !== 'image'" class="inline-bar">
          <span>改这一段</span>
          <a-button size="small" :loading="suggesting" @click="requestBlock('clarify')">改清楚</a-button>
          <a-button size="small" :loading="suggesting" @click="requestBlock('shorten')">写短些</a-button>
          <a-button size="small" :loading="suggesting" @click="requestBlock('change_voice')">换口吻</a-button>
          <a-input
            v-model:value="customNote"
            size="small"
            placeholder="或写一句：少说教 / 更具体"
            @press-enter="requestBlock('clarify', customNote)"
          />
          <a-button size="small" type="primary" :loading="suggesting" :disabled="!customNote.trim()" @click="requestBlock('clarify', customNote)">
            按这句话改
          </a-button>
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
            <a-button type="primary" size="small" aria-label="采用这段建议" @click="emit('accept', pendingFor(block)!)">采用</a-button>
            <a-button size="small" aria-label="不用这段建议" @click="emit('reject', pendingFor(block)!)">不用</a-button>
          </div>
        </div>
      </section>
      <p v-if="!blocks.length" class="empty">还没有正文。</p>
    </article>
  </section>
</template>

<style scoped>
.workbench { display: grid; gap: 14px; }
.toolbar, .toolbar-actions { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; }
.hint { margin: 0; color: #7a6650; font-size: 13px; }
.notice { margin: 0; }
.whole-box, .source, .inline-bar, .compare { padding: 12px; border: 1px solid #e8e1d5; border-radius: 10px; background: #fffdf8; }
.whole-box { display: grid; gap: 10px; }
.whole-box label { display: grid; gap: 6px; color: #5c564f; font-size: 13px; font-weight: 600; }
.source { display: grid; gap: 10px; }
.article { padding: 8px 4px 24px; }
.article h1 { margin: 0 0 18px; color: #1f1c1a; font-family: Georgia, 'Songti SC', serif; font-size: 32px; line-height: 1.25; }
.block { margin: 0 0 8px; border-radius: 8px; }
.block-body { padding: 6px 10px; border-radius: 8px; cursor: pointer; }
.block-body :deep(p), .block-body :deep(h2), .block-body :deep(li) { margin: 0; line-height: 1.85; }
.block-body :deep(img) { display: block; width: 100%; height: auto; border-radius: 6px; }
.block.image .block-body { cursor: default; padding: 0; }
.block.active .block-body, .block-body:hover { background: #f6efe4; }
.block.pending { outline: 1px solid #c7b497; }
.inline-bar { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin: 8px 10px 12px; }
.inline-bar span { color: #7a6650; font-size: 12px; font-weight: 700; }
.inline-bar :deep(.ant-input) { max-width: 240px; }
.compare { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin: 8px 10px 14px; }
.compare.whole { margin: 0; }
.compare pre { margin: 8px 0 0; max-height: 240px; overflow: auto; white-space: pre-wrap; color: #3f3a35; font-family: inherit; font-size: 14px; line-height: 1.7; }
.compare-actions { grid-column: 1 / -1; display: flex; gap: 8px; }
.empty { color: #948d84; }
@media (max-width: 760px) { .compare { grid-template-columns: 1fr; } }
</style>
