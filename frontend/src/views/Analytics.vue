<script setup lang="ts">
// M11-D 数据看板：4 tab + 时间窗（近 7/14/30 日 / 全量）
// - tab 1 仪表盘:周报 + LLM 成本 + 平台汇总（复用 M10 已存接口）
// - tab 2 账号数据:GET /analytics/accounts（按 account × platform 汇总）
// - tab 3 作品数据:GET /analytics/contents（按 content × title 汇总）
// - tab 4 排行榜:GET /analytics/leaderboard（按 metric 排序）
// 全部只读,不改 schema,不改契约
import { computed, onMounted, ref, watch } from 'vue'
import { useAnalyticsStore } from '../stores'
import { storeToRefs } from 'pinia'

const store = useAnalyticsStore()
const { weekly, cost, platforms } = storeToRefs(store)

// 4 tab + 1 时间窗
const activeTab = ref<'dashboard' | 'accounts' | 'contents' | 'leaderboard'>('dashboard')
const windowDays = ref<number | null>(null) // null = 全部

// 顶部全局钩子
const accountsData = ref<{ items: AccountRow[]; days: number | null } | null>(null)
const contentsData = ref<{ items: ContentRow[]; days: number | null } | null>(null)
const leaderboardData = ref<{ items: LeaderboardRow[]; metric: string } | null>(null)
const tabLoading = ref(false)
const tabError = ref<string | null>(null)

interface AccountRow {
  platform: string
  account: string
  publications: number
  latest_views: number
  latest_likes: number
  latest_comments: number
  latest_shares: number
}
interface ContentRow {
  content_id: string
  title: string | null
  publications: number
  latest_views: number
  latest_likes: number
  latest_comments: number
  latest_shares: number
}
interface LeaderboardRow {
  platform: string
  publications: number
  latest_views: number
  latest_likes: number
  latest_comments: number
  latest_shares: number
}

const WINDOW_OPTIONS: ReadonlyArray<{ label: string; value: number | null }> = [
  { label: '全部', value: null },
  { label: '近 7 日', value: 7 },
  { label: '近 14 日', value: 14 },
  { label: '近 30 日', value: 30 },
]

const LEADERBOARD_METRIC_OPTIONS: ReadonlyArray<{ label: string; value: string }> = [
  { label: '总播放', value: 'latest_views' },
  { label: '点赞', value: 'latest_likes' },
  { label: '评论', value: 'latest_comments' },
  { label: '分享', value: 'latest_shares' },
  { label: '发布数', value: 'publications' },
]
const leaderboardMetric = ref('latest_views')

async function loadTab() {
  tabError.value = null
  tabLoading.value = true
  try {
    const params = new URLSearchParams()
    if (windowDays.value !== null) params.set('days', String(windowDays.value))
    const qs = params.toString()
    if (activeTab.value === 'accounts') {
      const r = await fetch(`/api/v1/analytics/accounts${qs ? '?' + qs : ''}`)
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      accountsData.value = await r.json() as { items: AccountRow[]; days: number | null }
    } else if (activeTab.value === 'contents') {
      const r = await fetch(`/api/v1/analytics/contents${qs ? '?' + qs : ''}`)
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      contentsData.value = await r.json() as { items: ContentRow[]; days: number | null }
    } else if (activeTab.value === 'leaderboard') {
      const r = await fetch(
        `/api/v1/analytics/leaderboard?metric=${leaderboardMetric.value}`
        + (qs ? '&' + qs : ''),
      )
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      leaderboardData.value = await r.json() as { items: LeaderboardRow[]; metric: string }
    }
  } catch (e: unknown) {
    tabError.value = e instanceof Error ? e.message : String(e)
  } finally {
    tabLoading.value = false
  }
}

async function loadBase() {
  await store.loadAll()
}

onMounted(async () => {
  await loadBase()
  // 默认 tab=仪表盘,不需要额外 load
})

watch(activeTab, () => {
  if (activeTab.value !== 'dashboard') loadTab()
})
watch([windowDays, leaderboardMetric], () => {
  if (activeTab.value !== 'dashboard') loadTab()
})

const dashboardHasData = computed(() => Boolean(
  weekly.value || cost.value || platforms.value,
))
</script>

<template>
  <div>
    <h2>数据看板</h2>

    <!-- 顶部全局时间窗(除「排行榜」还可换 metric) -->
    <a-row style="margin-bottom: 12px" align="middle" :gutter="8">
      <a-col>
        <a-radio-group v-model:value="windowDays" button-style="solid">
          <a-radio-button
            v-for="opt in WINDOW_OPTIONS"
            :key="String(opt.value)"
            :value="opt.value"
          >
            {{ opt.label }}
          </a-radio-button>
        </a-radio-group>
      </a-col>
      <a-col v-if="activeTab === 'leaderboard'">
        <a-select v-model:value="leaderboardMetric" style="width: 140px">
          <a-select-option
            v-for="m in LEADERBOARD_METRIC_OPTIONS"
            :key="m.value"
            :value="m.value"
          >
            按 {{ m.label }}
          </a-select-option>
        </a-select>
      </a-col>
    </a-row>

    <a-tabs v-model:active-key="activeTab">
      <!-- ── tab 1 仪表盘(只读,复用 M10 接口) ── -->
      <a-tab-pane key="dashboard" title="仪表盘">
        <a-spin :spinning="store.loading">
          <a-empty v-if="!dashboardHasData && !store.loading" description="暂无数据" />
          <a-row v-else :gutter="16">
            <a-col :span="12">
              <a-card title="LLM 成本(按 stage)" style="margin-bottom: 16px">
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
      </a-tab-pane>

      <!-- ── tab 2 账号数据 ── -->
      <a-tab-pane key="accounts" title="账号数据">
        <a-spin :spinning="tabLoading">
          <a-alert v-if="tabError" type="error" :message="tabError" show-icon />
          <a-table
            v-else
            :data-source="accountsData?.items || []"
            :columns="[
              { title: 'platform', dataIndex: 'platform', width: 110 },
              { title: 'account', dataIndex: 'account', width: 160 },
              { title: '发布数', dataIndex: 'publications', width: 90 },
              { title: '播放', dataIndex: 'latest_views', width: 90 },
              { title: '点赞', dataIndex: 'latest_likes', width: 90 },
              { title: '评论', dataIndex: 'latest_comments', width: 90 },
              { title: '分享', dataIndex: 'latest_shares', width: 90 },
            ]"
            :pagination="{ pageSize: 50 }"
            row-key="account"
            size="small"
          />
        </a-spin>
      </a-tab-pane>

      <!-- ── tab 3 作品数据 ── -->
      <a-tab-pane key="contents" title="作品数据">
        <a-spin :spinning="tabLoading">
          <a-alert v-if="tabError" type="error" :message="tabError" show-icon />
          <a-table
            v-else
            :data-source="contentsData?.items || []"
            :columns="[
              { title: 'id', dataIndex: 'content_id', width: 110 },
              { title: 'title', dataIndex: 'title' },
              { title: '发布数', dataIndex: 'publications', width: 90 },
              { title: '播放', dataIndex: 'latest_views', width: 90 },
              { title: '点赞', dataIndex: 'latest_likes', width: 90 },
              { title: '评论', dataIndex: 'latest_comments', width: 90 },
              { title: '分享', dataIndex: 'latest_shares', width: 90 },
            ]"
            :pagination="{ pageSize: 50 }"
            row-key="content_id"
            size="small"
          >
            <template #bodyCell="{ column, record }">
              <template v-if="column.dataIndex === 'title'">
                <a :href="`/contents/${record.content_id}`">
                  {{ record.title ?? record.content_id }}
                </a>
              </template>
            </template>
          </a-table>
        </a-spin>
      </a-tab-pane>

      <!-- ── tab 4 排行榜 ── -->
      <a-tab-pane key="leaderboard" title="排行榜">
        <a-spin :spinning="tabLoading">
          <a-alert v-if="tabError" type="error" :message="tabError" show-icon />
          <a-table
            v-else
            :data-source="leaderboardData?.items || []"
            :columns="[
              { title: '排名', key: 'rank', width: 60 },
              { title: 'platform', dataIndex: 'platform', width: 120 },
              { title: '发布数', dataIndex: 'publications', width: 100 },
              { title: '播放', dataIndex: 'latest_views', width: 100 },
              { title: '点赞', dataIndex: 'latest_likes', width: 100 },
              { title: '评论', dataIndex: 'latest_comments', width: 100 },
              { title: '分享', dataIndex: 'latest_shares', width: 100 },
            ]"
            :pagination="false"
            row-key="platform"
            size="small"
          >
            <template #bodyCell="{ column, index }">
              <template v-if="column.key === 'rank'">
                {{ (index ?? 0) + 1 }}
              </template>
            </template>
          </a-table>
        </a-spin>
      </a-tab-pane>
    </a-tabs>
  </div>
</template>
