<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, apiPost, unwrapError, GENERATION_TIMEOUT_MS } from '../api/client'

interface AccountProfile {
  id: string
  platform: string
  display_name: string
  positioning: string
  audience: string
  style: string
  operations_enabled: boolean
}

interface Suggestion {
  positioning: string
  audience: string
  style: string
  rationale: string
}

const items = ref<AccountProfile[]>([])
const selectedId = ref('')
const viewpoint = ref('')
const reader = ref('')
const mustNot = ref('')
const sourcesText = ref('')
const suggestion = ref<Suggestion | null>(null)
const unread = ref<string[]>([])
const error = ref('')
const loading = ref(false)

async function load(): Promise<void> {
  error.value = ''
  const response = await api.get<{ items: AccountProfile[] }>('/account-profiles')
  items.value = response.data.items
  if (!selectedId.value && items.value[0]) selectedId.value = items.value[0].id
}

async function importFromConfig(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    await apiPost('/account-profiles/import')
    await load()
  } catch (err) {
    error.value = unwrapError(err)
  } finally {
    loading.value = false
  }
}

async function propose(): Promise<void> {
  if (!selectedId.value) return
  loading.value = true
  error.value = ''
  suggestion.value = null
  try {
    const sources = sourcesText.value.split('\n').map(item => item.trim()).filter(Boolean)
    const response = await apiPost<{ suggestion: Suggestion; unread_sources: string[] }>(
      `/account-profiles/${selectedId.value}/onboarding`,
      { interview: { viewpoint: viewpoint.value, reader: reader.value, must_not: mustNot.value }, sources },
      GENERATION_TIMEOUT_MS,
    )
    suggestion.value = response.data.suggestion
    unread.value = response.data.unread_sources
  } catch (err) {
    error.value = unwrapError(err)
  } finally {
    loading.value = false
  }
}

async function confirm(): Promise<void> {
  if (!selectedId.value) return
  loading.value = true
  error.value = ''
  try {
    await apiPost(`/account-profiles/${selectedId.value}/onboarding/confirm`)
    suggestion.value = null
    await load()
  } catch (err) {
    error.value = unwrapError(err)
  } finally {
    loading.value = false
  }
}

onMounted(() => { load().catch(err => { error.value = unwrapError(err) }) })
</script>

<template>
  <section class="studio">
    <header>
      <p class="eyebrow">账号</p>
      <h1>先确认这个号为谁写、不能写什么。</h1>
      <p>建议不会写入档案，除非你点确认。运营开关默认关闭。</p>
    </header>
    <a-alert v-if="error" type="error" :message="error" show-icon class="notice" />
    <a-space class="toolbar">
      <a-button :loading="loading" @click="importFromConfig">从配置导入账号（不开启运营）</a-button>
      <a-select v-if="items.length" v-model:value="selectedId" style="min-width: 240px">
        <a-select-option v-for="item in items" :key="item.id" :value="item.id">
          {{ item.display_name }} · {{ item.platform }}
        </a-select-option>
      </a-select>
    </a-space>
    <a-card v-if="selectedId" :bordered="false" class="panel">
      <p class="current">当前定位：{{ items.find(item => item.id === selectedId)?.positioning }}</p>
      <a-form layout="vertical">
        <a-form-item label="你的观点"><a-textarea v-model:value="viewpoint" :rows="3" /></a-form-item>
        <a-form-item label="读者是谁"><a-input v-model:value="reader" /></a-form-item>
        <a-form-item label="不能写什么"><a-input v-model:value="mustNot" /></a-form-item>
        <a-form-item label="参考链接（一行一个）"><a-textarea v-model:value="sourcesText" :rows="3" /></a-form-item>
        <a-space>
          <a-button type="primary" :loading="loading" @click="propose">生成定位建议</a-button>
          <a-button :disabled="!suggestion" :loading="loading" @click="confirm">确认写入档案</a-button>
        </a-space>
      </a-form>
      <aside v-if="suggestion" class="suggestion">
        <h2>待确认建议</h2>
        <p><strong>定位</strong> {{ suggestion.positioning }}</p>
        <p><strong>读者</strong> {{ suggestion.audience }}</p>
        <p><strong>文风</strong> {{ suggestion.style }}</p>
        <p>{{ suggestion.rationale }}</p>
        <p v-if="unread.length">未读来源：{{ unread.join('；') }}</p>
      </aside>
    </a-card>
  </section>
</template>

<style scoped>
.studio { max-width: 760px; padding: 24px 0 64px; }
.eyebrow { color: #7a6650; font-size: 12px; font-weight: 700; letter-spacing: .08em; }
h1 { font-family: Georgia, 'Songti SC', serif; font-size: 32px; line-height: 1.2; }
.notice, .toolbar, .panel { margin: 16px 0; }
.panel { background: #fffdf8; border: 1px solid #e8e1d5; }
.current { color: #706b65; }
.suggestion { margin-top: 24px; padding: 16px; background: #f5f2ed; }
</style>
