<script setup lang="ts">
// M10-8 Review：审核台（gated 内容卡片流 + 写操作 P2 禁用）
import { onMounted } from 'vue'
import { useReviewStore } from '../stores'
import { storeToRefs } from 'pinia'

const store = useReviewStore()
const { items, loading, error } = storeToRefs(store)
onMounted(() => store.load())
</script>

<template>
  <h2>审核台（{{ items.length }} 篇待审）</h2>
  <a-alert v-if="error" type="error" :message="error" show-icon style="margin-bottom: 16px" />
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
          <template #actions>
            <a-button type="primary" disabled>approve（P2）</a-button>
            <a-button danger disabled>reject（P2）</a-button>
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
