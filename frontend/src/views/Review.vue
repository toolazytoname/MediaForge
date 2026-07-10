<script setup lang="ts">
// M10-8 Review：审核台（gated 内容卡片流）
// M10 P2 阶段 C：approve / reject 按钮解 disabled，调 POST /api/v1/review/{id}
import { onMounted, ref } from 'vue'
import { useReviewStore, useReviewActionStore } from '../stores'
import { storeToRefs } from 'pinia'

const store = useReviewStore()
const actionStore = useReviewActionStore()
const { items, loading, error } = storeToRefs(store)
const success = ref<string | null>(null)
const errorAlert = ref<{ code: string; msg: string; contentId: string } | null>(null)
const rejectReasonByContent = ref<Record<string, string>>({})

onMounted(() => store.load())

async function onApprove(contentId: string) {
  success.value = null
  errorAlert.value = null
  const r = await actionStore.run(contentId, 'approve')
  if (r) {
    success.value = `已 approve: ${r.id} → ${r.status}`
    await store.load()
  } else {
    const [code, ...rest] = (actionStore.lastError ?? '').split(':')
    errorAlert.value = {
      code: code ?? 'unknown',
      msg: rest.join(':').trim(),
      contentId,
    }
  }
}

async function onReject(contentId: string) {
  success.value = null
  errorAlert.value = null
  const reason = rejectReasonByContent.value[contentId] ?? ''
  const r = await actionStore.run(contentId, 'reject', reason)
  if (r) {
    success.value = `已 reject: ${r.id} → ${r.status}${reason ? `（理由：${reason}）` : ''}`
    delete rejectReasonByContent.value[contentId]
    await store.load()
  } else {
    const [code, ...rest] = (actionStore.lastError ?? '').split(':')
    errorAlert.value = {
      code: code ?? 'unknown',
      msg: rest.join(':').trim(),
      contentId,
    }
  }
}
</script>

<template>
  <h2>审核台（{{ items.length }} 篇待审）</h2>
  <a-alert v-if="error" type="error" :message="error" show-icon style="margin-bottom: 16px" />
  <a-alert
    v-if="success"
    type="success"
    :message="success"
    show-icon
    closable
    style="margin-bottom: 16px"
    @close="success = null"
  />
  <a-alert
    v-if="errorAlert"
    type="error"
    :message="`决策失败 (${errorAlert.contentId}): ${errorAlert.code} - ${errorAlert.msg}`"
    show-icon
    closable
    style="margin-bottom: 16px"
    @close="errorAlert = null"
  />
  <a-spin :spinning="loading">
    <a-empty v-if="!loading && items.length === 0" description="无待审内容" />
    <a-row :gutter="[16, 16]">
      <a-col v-for="c in items" :key="c.id" :span="12">
        <a-card>
          <template #title>
            <router-link :to="`/contents/${c.id}`">{{ c.title }}</router-link>
            <a-tag color="blue" style="margin-left: 8px">{{ c.pillar }}</a-tag>
            <a-tag>门禁分 {{ c.gate_score_total ?? 'N/A' }}</a-tag>
          </template>
          <p v-if="c.gate_verdict" style="color: #666">{{ c.gate_verdict }}</p>
          <div v-html="c.canonical_html" class="md-body" style="max-height: 300px; overflow: auto" />
          <a-input
            v-model:value="rejectReasonByContent[c.id]"
            placeholder="reject 理由（可选）"
            style="margin-bottom: 8px"
            size="small"
          />
          <template #actions>
            <a-button
              type="primary"
              :loading="actionStore.running"
              @click="onApprove(c.id)"
            >
              approve
            </a-button>
            <a-button
              danger
              :loading="actionStore.running"
              style="margin-left: 8px"
              @click="onReject(c.id)"
            >
              reject
            </a-button>
          </template>
        </a-card>
      </a-col>
    </a-row>
  </a-spin>
</template>

<style scoped>
.md-body :deep(h1) { font-size: 20px; margin: 12px 0 6px; }
.md-body :deep(h2) { font-size: 18px; margin: 10px 0 4px; }
.md-body :deep(p) { line-height: 1.6; margin: 6px 0; }
</style>