<script setup lang="ts">
// M10-8 Dashboard：驾驶舱（计数/成本预算/待办/近期活动/门禁直方图）
import { onMounted, computed } from 'vue'
import { useDashboardStore } from '../stores'
import { storeToRefs } from 'pinia'
import { formatDateTime } from '../utils/format'

const store = useDashboardStore()
const { data, loading, error } = storeToRefs(store)
onMounted(() => store.load())

const budgetPct = computed(() => {
  if (!data.value?.budget.monthly_usd) return 0
  return Math.min(100, Math.round(data.value.budget.used_ratio * 100))
})

const budgetColor = computed(() => {
  const r = data.value?.budget.used_ratio ?? 0
  if (r >= 1.0) return '#cf1322'
  if (r >= 0.8) return '#d4b106'
  return '#389e0d'
})
</script>

<template>
  <a-spin :spinning="loading">
    <a-alert v-if="error" type="error" :message="error" show-icon style="margin-bottom: 16px" />

    <template v-if="data">
      <a-row :gutter="16" style="margin-bottom: 16px">
        <a-col :span="6">
          <a-card title="本月 LLM 花费">
            <div style="font-size: 28px; font-weight: bold">
              ${{ data.budget.used_usd.toFixed(4) }}
            </div>
            <a-progress
              :percent="budgetPct"
              :stroke-color="budgetColor"
              :format="(p: number) => `${p}% / $${data!.budget.monthly_usd}`"
            />
          </a-card>
        </a-col>
        <a-col :span="6">
          <a-card title="🔴 待审">
            <a href="/review">
              <div style="font-size: 32px; font-weight: bold; color: #cf1322">
                {{ data.todos.to_review }}
              </div>
            </a>
            <div style="color: #999">gated 内容</div>
          </a-card>
        </a-col>
        <a-col :span="6">
          <a-card title="🟡 待发布">
            <a href="/publish/calendar">
              <div style="font-size: 32px; font-weight: bold; color: #d4b106">
                {{ data.todos.to_publish }}
              </div>
            </a>
            <div style="color: #999">queued</div>
          </a-card>
        </a-col>
        <a-col :span="6">
          <a-card title="⚠️ 发布失败">
            <a href="/publish/records?status=failed">
              <div style="font-size: 32px; font-weight: bold; color: #d4380d">
                {{ data.todos.publish_failed }}
              </div>
            </a>
            <div style="color: #999">需重试</div>
          </a-card>
        </a-col>
      </a-row>

      <a-row :gutter="16" style="margin-bottom: 16px">
        <a-col :span="12">
          <a-card title="各表状态计数">
            <a-table
              :data-source="['topics','contents','publications'].map(t => ({ table: t, ...data!.counts[t] }))"
              :columns="[{ title: '表', dataIndex: 'table' }].concat(
                Object.keys(data!.counts.topics || {}).map(s => ({ title: s, dataIndex: s }))
              )"
              :pagination="false"
              size="small"
            />
          </a-card>
        </a-col>
        <a-col :span="12">
          <a-card title="门禁分直方图">
            <a-table
              :data-source="data.gate_histogram"
              :columns="[
                { title: '分数段', dataIndex: 'score_range' },
                { title: '数', dataIndex: 'count' },
              ]"
              :pagination="false"
              size="small"
            />
            <p v-if="data.gate_correlation !== null">
              Pearson r = {{ data.gate_correlation.toFixed(3) }}
              <span v-if="Math.abs(data.gate_correlation) < 0.2" style="color: #d4b106">
                (|r| < 0.2 — 重新校准锚点)
              </span>
            </p>
          </a-card>
        </a-col>
      </a-row>

      <a-card title="近期活动（最近 20 条）">
        <a-table
          :data-source="data.activity"
          :columns="[
            { title: 'kind', dataIndex: 'kind' },
            { title: 'id', dataIndex: 'id' },
            { title: 'status', dataIndex: 'status' },
            { title: 'updated_at', dataIndex: 'updated_at' },
          ]"
          :pagination="false"
          size="small"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.dataIndex === 'updated_at'">
              {{ formatDateTime(record.updated_at) }}
            </template>
          </template>
        </a-table>
      </a-card>
    </template>
    <a-empty v-else-if="!loading" description="加载中..." />
  </a-spin>
</template>
