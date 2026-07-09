<script setup lang="ts">
// M10-8 ContentDetail：内容详情（canonical HTML + 派生文件 + 图卡 + 出版时间线）
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useContentsStore, type ContentDetail } from '../stores'

const route = useRoute()
const store = useContentsStore()
const data = ref<ContentDetail | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)

onMounted(async () => {
  loading.value = true
  error.value = null
  try {
    data.value = await store.getDetail(route.params.id as string)
  } catch (e) {
    error.value = String(e)
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <a-spin :spinning="loading">
    <a-alert v-if="error" type="error" :message="error" show-icon style="margin-bottom: 16px" />
    <template v-if="data">
      <h2>{{ data.title }}</h2>
      <a-descriptions :column="2" size="small" bordered style="margin-bottom: 16px">
        <a-descriptions-item label="id">{{ data.id }}</a-descriptions-item>
        <a-descriptions-item label="status">
          <a-tag>{{ data.status }}</a-tag>
        </a-descriptions-item>
        <a-descriptions-item label="pillar">{{ data.pillar }}</a-descriptions-item>
        <a-descriptions-item label="formats">
          <a-tag v-for="f in data.formats" :key="f" color="blue">{{ f }}</a-tag>
        </a-descriptions-item>
        <a-descriptions-item v-if="data.gate_score_total" label="门禁分">
          {{ data.gate_score_total }}
        </a-descriptions-item>
        <a-descriptions-item v-if="data.gate_verdict" label="verdict">
          {{ data.gate_verdict }}
        </a-descriptions-item>
      </a-descriptions>

      <a-row :gutter="16">
        <a-col :span="14">
          <a-card title="canonical 预览">
            <div v-html="data.canonical_html" class="md-body" />
            <p v-if="!data.canonical_html" style="color: #999">（无 canonical.md 或为空）</p>
          </a-card>
        </a-col>
        <a-col :span="10">
          <a-card title="派生文件 + 图卡" style="margin-bottom: 16px">
            <a-list size="small" :data-source="data.files" :pagination="{ pageSize: 8 }">
              <template #renderItem="{ item }">
                <a-list-item>
                  <a-tag v-if="item.platform" color="purple">{{ item.platform }}</a-tag>
                  <a-tag color="default">{{ item.kind }}</a-tag>
                  <span style="margin-left: 8px">{{ item.path }}</span>
                  <a-tag v-if="!item.exists" color="red" style="margin-left: 8px">缺</a-tag>
                  <a-tag v-else color="green" style="margin-left: 8px">{{ item.size }}B</a-tag>
                </a-list-item>
              </template>
            </a-list>
          </a-card>

          <a-card v-if="data.images.cover || data.images.inline.length" title="图卡预览" style="margin-bottom: 16px">
            <a-image v-if="data.images.cover" :src="data.images.cover" :width="200" />
            <div v-if="data.images.inline.length" style="margin-top: 8px; display: flex; gap: 8px; flex-wrap: wrap">
              <a-image v-for="(u, i) in data.images.inline" :key="i" :src="u" :width="100" />
            </div>
          </a-card>

          <a-card title="排期（publications）">
            <a-list size="small" :data-source="data.publications">
              <template #renderItem="{ item }">
                <a-list-item>
                  <a-tag color="blue">{{ item.platform }}</a-tag>
                  <span style="margin-left: 8px">{{ item.scheduled_at }}</span>
                  <a-tag style="margin-left: 8px">{{ item.status }}</a-tag>
                </a-list-item>
              </template>
              <template #empty>
                <span style="color: #999">无排期</span>
              </template>
            </a-list>
          </a-card>
        </a-col>
      </a-row>
    </template>
  </a-spin>
</template>

<style scoped>
.md-body :deep(h1) { font-size: 24px; margin: 16px 0 8px; }
.md-body :deep(h2) { font-size: 20px; margin: 14px 0 6px; }
.md-body :deep(p) { line-height: 1.7; margin: 8px 0; }
.md-body :deep(code) { background: #f5f5f5; padding: 2px 4px; border-radius: 2px; }
</style>
