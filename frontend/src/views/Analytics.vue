<script setup lang="ts">
// M10-8 Analytics：周报 + LLM 成本 + 平台对比
import { onMounted } from 'vue'
import { useAnalyticsStore } from '../stores'
import { storeToRefs } from 'pinia'

const store = useAnalyticsStore()
const { weekly, cost, platforms, loading } = storeToRefs(store)
onMounted(() => store.loadAll())
</script>

<template>
  <h2>数据看板</h2>
  <a-spin :spinning="loading">
    <a-row :gutter="16">
      <a-col :span="12">
        <a-card title="LLM 成本（按 stage）" style="margin-bottom: 16px">
          <a-table
            :data-source="cost?.items || []"
            :columns="[
              { title: 'stage', dataIndex: 'stage' },
              { title: 'calls', dataIndex: 'calls', width: 80 },
              { title: 'cost_usd', dataIndex: 'cost_usd', width: 120 },
            ]"
            :pagination="false"
            size="small"
          />
        </a-card>
      </a-col>
      <a-col :span="12">
        <a-card title="平台汇总" style="margin-bottom: 16px">
          <a-table
            :data-source="platforms?.items || []"
            :columns="[
              { title: 'platform', dataIndex: 'platform' },
              { title: 'publications', dataIndex: 'publications', width: 100 },
              { title: 'views', dataIndex: 'latest_views', width: 80 },
              { title: 'likes', dataIndex: 'latest_likes', width: 80 },
              { title: 'comments', dataIndex: 'latest_comments', width: 100 },
            ]"
            :pagination="false"
            size="small"
          />
        </a-card>
      </a-col>
    </a-row>

    <a-card v-if="weekly" title="周报概览">
      <pre style="background: #f5f5f5; padding: 12px; border-radius: 4px; overflow: auto">{{ JSON.stringify(weekly.overview, null, 2) }}</pre>
    </a-card>
  </a-spin>
</template>
