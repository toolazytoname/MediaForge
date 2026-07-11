<script setup lang="ts">
// M11-B 发布中心：合并 PublishRecords + PublishCalendar 为蚁小二式单页面
// - 3 tab:【发布记录｜草稿箱｜日历】
// - 顶部工具条：[新增发布] 按钮 + 4 筛选（发布人/平台/状态/模式）
// - 「新增发布」= 草稿箱 tab 顶 + 顶部按钮两入口，调 POST /contents/{id}/schedule
// - 「日历」tab = 原 PublishCalendar 周视图
// - 「草稿箱」= approved 内容每行一个 [加入排期] 按钮
// 写操作全部走已有 JSON 端点，不新造裸 SQL，不绕 safe_publish
import { computed, defineComponent, h, onMounted, ref, type PropType } from 'vue'
import { formatDateTime } from '../utils/format'
import {
  usePublishStore,
  usePreviewStore,
  usePubActionStore,
  useScheduleStore,
  useAccountsStore,
  useSettingsStore,
  useContentsStore,
  type AccountHealthItem,
  type CalendarData,
  type ContentItem,
  type PreviewResult,
  type PublicationItem,
  type SchedulePayload,
} from '../stores'
import { storeToRefs } from 'pinia'

const publishStore = usePublishStore()
const previewStore = usePreviewStore()
const actionStore = usePubActionStore()
const scheduleStore = useScheduleStore()
const accountsStore = useAccountsStore()
const settingsStore = useSettingsStore()
const contentsStore = useContentsStore()

const { records, calendar, loading } = storeToRefs(publishStore)
const { items: accountList, loading: accountsLoading } = storeToRefs(accountsStore)
const { items: contentItems } = storeToRefs(contentsStore)

// 4 筛选（status + platform + account_id + pending_only），全部只读 GET 参数
const filters = ref<{
  status?: string
  platform?: string
  account_id?: string
  mode?: 'all' | 'pending' | 'done'
}>({ mode: 'all' })

// 当前 tab：records | drafts | calendar
const activeTab = ref<'records' | 'drafts' | 'calendar'>('records')

// 日历 tab:周锚点
const calendarWeek = ref<string | undefined>(undefined)

// 「新增发布」modal
const newPublishOpen = ref(false)
const newPublishContentId = ref<string | undefined>(undefined)
const newPublishPlatform = ref<string | undefined>(undefined)
const newPublishAccountId = ref<string | undefined>(undefined)
const newPublishAt = ref<string>('2026-07-12T18:30')
const newPublishError = ref<string | null>(null)
const newPublishSuccess = ref<string | null>(null)

// Preview Drawer 状态
const previewDrawerOpen = ref(false)
const previewRunning = ref<string | null>(null)
const previewError = ref<string | null>(null)

// 草稿箱 tab:重排排期 inline modal（共用 newPublish 字段）
const inlineDraftId = ref<string | null>(null)
function openInlineSchedule(contentId: string) {
  inlineDraftId.value = contentId
  newPublishContentId.value = contentId
  newPublishPlatform.value = undefined
  newPublishAccountId.value = undefined
  newPublishAt.value = '2026-07-12T18:30'
  newPublishError.value = null
  newPublishSuccess.value = null
  newPublishOpen.value = true
}
function openGlobalNewPublish() {
  inlineDraftId.value = null
  newPublishContentId.value = undefined
  newPublishPlatform.value = undefined
  newPublishAccountId.value = undefined
  newPublishAt.value = '2026-07-12T18:30'
  newPublishError.value = null
  newPublishSuccess.value = null
  newPublishOpen.value = true
}

// 加载辅助
async function loadAll() {
  if (!settingsStore.config) {
    await settingsStore.load()
  }
  if (accountsStore.items.length === 0 && !accountsLoading.value) {
    accountsStore.load().catch(() => null)
  }
  loadRecords()
}

function loadRecords() {
  const f = filters.value
  const params: Record<string, string | number | boolean> = { with_metric: true }
  if (f.status) params.status = f.status
  if (f.platform) params.platform = f.platform
  if (f.account_id) params.account_id = f.account_id
  if (f.mode === 'pending') params.pending_only = true
  publishStore.loadRecords(params)
}

function loadDrafts() {
  contentsStore.load({ status: 'approved', limit: 50, offset: 0 })
}

function loadCalendar() {
  publishStore.loadCalendar(calendarWeek.value)
}

onMounted(async () => {
  await loadAll()
})

// 4 筛选派生
const platformOptions = computed(() => {
  const s = new Set<string>()
  for (const it of accountList.value) {
    if (it.platform) s.add(it.platform)
  }
  return Array.from(s).sort()
})

const accountOptions = computed(() => {
  return (accountList.value as AccountHealthItem[])
    .map((it) => ({ value: it.account, label: `${it.account}（${it.platform}）` }))
})

const draftContents = computed(() =>
  (contentItems.value as ContentItem[]).filter((c) => c.status === 'approved'),
)

const publishEnabled = computed(() => {
  const cfg = settingsStore.config as { publish?: { enabled?: boolean } } | null
  return cfg?.publish?.enabled !== false
})

const canSubmitNewPublish = computed(() => Boolean(
  newPublishContentId.value && newPublishPlatform.value
    && newPublishAccountId.value && newPublishAt.value,
))

// 「新增发布」= 草稿箱弹窗或顶部按钮两入口共用此 submit
async function submitNewPublish() {
  if (!canSubmitNewPublish.value) return
  const contentId = newPublishContentId.value as string
  const payload: SchedulePayload = {
    platform: newPublishPlatform.value as string,
    account_id: newPublishAccountId.value as string,
    scheduled_at: (newPublishAt.value.length === 16
      ? `${newPublishAt.value}:00+00:00`
      : `${newPublishAt.value}+00:00`),
  }
  const r = await scheduleStore.run(contentId, payload)
  if (r) {
    newPublishSuccess.value =
      `已加入排期：${r.platform} / ${r.account_id} @ ${r.scheduled_at}`
    newPublishError.value = null
    // 关闭 modal:草稿箱/记录都要刷新
    setTimeout(() => {
      newPublishOpen.value = false
      loadDrafts()
      loadRecords()
    }, 800)
  } else {
    newPublishError.value = scheduleStore.lastError ?? '排期失败'
  }
}

// ── 记录 tab 行操作 ──
async function onReschedule(pubId: string, currentTime: string) {
  const newTime = window.prompt('改排期时间 (ISO8601 UTC)', currentTime)
  if (!newTime) return
  const r = await actionStore.reschedule(pubId, newTime)
  if (r) {
    loadRecords()
  } else {
    window.alert(`reschedule 失败: ${actionStore.lastError ?? '未知'}`)
  }
}

async function onCancel(pubId: string) {
  if (!window.confirm('确认 cancel 这一条 scheduled publication？')) return
  const r = await actionStore.cancel(pubId)
  if (r) loadRecords()
  else window.alert(`cancel 失败: ${actionStore.lastError ?? '未知'}`)
}

async function onRetry(pubId: string) {
  const r = await actionStore.retry(pubId)
  if (r) loadRecords()
  else window.alert(`retry 失败: ${actionStore.lastError ?? '未知'}`)
}

async function onPreview(pubId: string) {
  previewError.value = null
  previewRunning.value = pubId
  try {
    const result = await previewStore.run(pubId)
    if (!result) {
      previewError.value = previewStore.lastError ?? '预演失败'
      return
    }
    previewDrawerOpen.value = true
  } finally {
    previewRunning.value = null
  }
}

function closePreviewDrawer() {
  previewDrawerOpen.value = false
  previewStore.reset()
}

function isQueued(item: PublicationItem): boolean {
  return item.status === 'queued'
}
function isFailed(item: PublicationItem): boolean {
  return item.status === 'failed'
}

// tab 切换→按需 lazy-load
function onTabChange(key: string | number) {
  const k = String(key)
  if (k === 'records') loadRecords()
  else if (k === 'drafts') loadDrafts()
  else if (k === 'calendar') loadCalendar()
}

// 内容标题展示（有 title 优先，否则 fallback）
function contentTitleById(id: string): string {
  const c = (contentItems.value as ContentItem[]).find((x) => x.id === id)
  return c?.title ?? id
}
</script>

<template>
  <div>
    <h2>发布中心</h2>

    <!-- 顶部工具条:左侧筛选 + 右侧[新增发布] -->
    <a-row style="margin-bottom: 12px" align="middle" :gutter="8">
      <a-col flex="auto">
        <a-space wrap>
          <a-select
            v-model:value="filters.status"
            placeholder="状态"
            allow-clear
            style="width: 140px"
            @change="loadRecords"
          >
            <a-select-option value="queued">queued</a-select-option>
            <a-select-option value="publishing">publishing</a-select-option>
            <a-select-option value="published">published</a-select-option>
            <a-select-option value="failed">failed</a-select-option>
            <a-select-option value="cancelled">cancelled</a-select-option>
          </a-select>
          <a-select
            v-model:value="filters.platform"
            placeholder="平台"
            allow-clear
            style="width: 140px"
            @change="loadRecords"
          >
            <a-select-option v-for="p in platformOptions" :key="p" :value="p">
              {{ p }}
            </a-select-option>
          </a-select>
          <a-select
            v-model:value="filters.account_id"
            placeholder="发布人(账号)"
            allow-clear
            show-search
            style="width: 200px"
            @change="loadRecords"
          >
            <a-select-option v-for="a in accountOptions" :key="a.value" :value="a.value">
              {{ a.label }}
            </a-select-option>
          </a-select>
          <a-select
            v-model:value="filters.mode"
            placeholder="模式"
            style="width: 120px"
            @change="loadRecords"
          >
            <a-select-option value="all">全部</a-select-option>
            <a-select-option value="pending">计划中</a-select-option>
            <a-select-option value="done">已完成</a-select-option>
          </a-select>
          <a-button @click="loadRecords">刷新记录</a-button>
        </a-space>
      </a-col>
      <a-col flex="none">
        <a-button type="primary" @click="openGlobalNewPublish">+ 新增发布</a-button>
      </a-col>
    </a-row>

    <a-alert
      v-if="!publishEnabled"
      type="warning"
      show-icon
      style="margin-bottom: 12px"
      message="publish.enabled=false：仅 dry-run,真实发布会被 safe_publish 拒绝。"
    />

    <!-- ── 3 tab ── -->
    <a-tabs v-model:active-key="activeTab" @change="onTabChange">
      <!-- tab 1: 发布记录 -->
      <a-tab-pane key="records" title="发布记录">
        <a-alert
          v-if="previewError"
          type="error"
          show-icon
          closable
          style="margin-bottom: 12px"
          :message="`预演失败：${previewError}`"
          @close="previewError = null"
        />
        <a-spin :spinning="loading">
          <a-table
            :data-source="records"
            :columns="[
              { title: 'id', dataIndex: 'id', width: 110 },
              { title: 'content', key: 'content', width: 200 },
              { title: 'platform', dataIndex: 'platform', width: 90 },
              { title: '账号', dataIndex: 'account_id', width: 110 },
              { title: 'scheduled_at', dataIndex: 'scheduled_at', width: 170 },
              { title: 'status', dataIndex: 'status', width: 100 },
              { title: 'views', key: 'views', width: 80 },
              { title: 'likes', key: 'likes', width: 80 },
              { title: 'actions', key: 'actions', width: 320 },
            ]"
            :pagination="{ pageSize: 50 }"
            row-key="id"
            size="small"
          >
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'content'">
                <a :href="`/contents/${record.content_id}`">{{ contentTitleById(record.content_id) }}</a>
              </template>
              <template v-else-if="column.dataIndex === 'scheduled_at'">
                {{ formatDateTime(record.scheduled_at) }}
              </template>
              <template v-else-if="column.key === 'views'">
                {{ record.latest_metric?.views ?? '—' }}
              </template>
              <template v-else-if="column.key === 'likes'">
                {{ record.latest_metric?.likes ?? '—' }}
              </template>
              <template v-else-if="column.key === 'actions'">
                <a-space size="small">
                  <a-button
                    size="small"
                    :loading="previewRunning === record.id"
                    :disabled="!isQueued(record) || previewStore.running"
                    @click="onPreview(record.id)"
                  >
                    🔍 预演
                  </a-button>
                  <a-button
                    size="small"
                    :disabled="!isQueued(record) || actionStore.running"
                    @click="onReschedule(record.id, record.scheduled_at)"
                  >
                    reschedule
                  </a-button>
                  <a-button
                    size="small"
                    danger
                    :disabled="!isQueued(record) || actionStore.running"
                    @click="onCancel(record.id)"
                  >
                    cancel
                  </a-button>
                  <a-button
                    v-if="isFailed(record)"
                    size="small"
                    type="primary"
                    :disabled="actionStore.running"
                    @click="onRetry(record.id)"
                  >
                    retry
                  </a-button>
                </a-space>
              </template>
            </template>
          </a-table>
        </a-spin>
      </a-tab-pane>

      <!-- tab 2: 草稿箱 -->
      <a-tab-pane key="drafts" title="草稿箱">
        <a-spin :spinning="loading">
          <a-table
            :data-source="draftContents"
            :columns="[
              { title: 'id', dataIndex: 'id', width: 110 },
              { title: 'title', dataIndex: 'title' },
              { title: 'pillar', dataIndex: 'pillar', width: 130 },
              { title: 'updated_at', dataIndex: 'updated_at', width: 170 },
              { title: 'actions', key: 'actions', width: 160 },
            ]"
            :pagination="{ pageSize: 50 }"
            row-key="id"
            size="small"
          >
            <template #bodyCell="{ column, record }">
              <template v-if="column.dataIndex === 'updated_at'">
                {{ formatDateTime(record.updated_at) }}
              </template>
              <template v-if="column.key === 'actions'">
                <a-space>
                  <a-button
                    size="small"
                    type="primary"
                    :disabled="scheduleStore.running"
                    @click="openInlineSchedule(record.id)"
                  >
                    + 排期
                  </a-button>
                </a-space>
              </template>
            </template>
          </a-table>
        </a-spin>
        <a-empty v-if="!loading && draftContents.length === 0" description="无 approved 待排期内容" />
      </a-tab-pane>

      <!-- tab 3: 日历 -->
      <a-tab-pane key="calendar" title="日历">
        <a-space style="margin-bottom: 12px">
          <a-input
            v-model:value="calendarWeek"
            placeholder="YYYY-MM-DD（可选）"
            allow-clear
            style="width: 200px"
            @press-enter="loadCalendar"
          />
          <a-button @click="loadCalendar">加载</a-button>
        </a-space>
        <a-spin :spinning="loading">
          <template v-if="calendar">
            <p>
              本周: {{ (calendar as CalendarData).week_start }} → {{ (calendar as CalendarData).week_end }}
            </p>
            <a-row :gutter="8">
              <a-col
                v-for="d in (calendar as CalendarData).days"
                :key="d.date"
                :span="3"
              >
                <a-card :title="d.date" size="small" style="margin-bottom: 8px">
                  <a-list
                    size="small"
                    :data-source="d.publications"
                    :pagination="{ pageSize: 5 }"
                  >
                    <template #renderItem="{ item }">
                      <a-list-item>
                        <a-tag color="purple">{{ item.platform }}</a-tag>
                        <span style="font-size: 12px">{{ item.scheduled_at.split('T')[1]?.slice(0, 5) }}</span>
                        <a-tag :color="item.status === 'published' ? 'green' : 'orange'">
                          {{ item.status }}
                        </a-tag>
                      </a-list-item>
                    </template>
                    <template #empty>
                      <span style="color: #ccc">无</span>
                    </template>
                  </a-list>
                </a-card>
              </a-col>
            </a-row>
          </template>
        </a-spin>
      </a-tab-pane>
    </a-tabs>

    <!-- ── 「新增发布」/ 草稿箱内联排期 共用 modal ── -->
    <a-modal
      v-model:open="newPublishOpen"
      title="新增发布"
      :ok-text="'加入排期'"
      :ok-button-props="{ loading: scheduleStore.running }"
      @ok="submitNewPublish"
    >
      <a-form layout="vertical">
        <a-form-item label="内容">
          <a-select
            v-model:value="newPublishContentId"
            placeholder="选 approved 内容"
            allow-clear
            show-search
            :filter-option="(input: string, opt: any) =>
              opt.label.toLowerCase().includes(input.toLowerCase())
            "
            :options="(draftContents as ContentItem[]).map((c) => ({
              value: c.id, label: `${c.title} (${c.id})`,
            }))"
            :disabled="inlineDraftId !== null"
          />
        </a-form-item>
        <a-form-item label="平台">
          <a-select
            v-model:value="newPublishPlatform"
            :options="platformOptions.map((p) => ({ value: p, label: p }))"
            placeholder="选平台"
            allow-clear
            @change="newPublishAccountId = undefined"
          />
        </a-form-item>
        <a-form-item label="账号">
          <a-select
            v-model:value="newPublishAccountId"
            :options="(accountList as AccountHealthItem[])
              .filter((it) => it.platform === newPublishPlatform)
              .map((it) => ({ value: it.account, label: it.account }))"
            :disabled="!newPublishPlatform"
            placeholder="选账号"
            allow-clear
          />
        </a-form-item>
        <a-form-item label="时间">
          <a-input
            v-model:value="newPublishAt"
            type="datetime-local"
            placeholder="YYYY-MM-DDTHH:MM"
          />
        </a-form-item>
        <a-alert
          v-if="newPublishError"
          type="error"
          :message="newPublishError"
          show-icon
          style="margin-top: 8px"
        />
        <a-alert
          v-if="newPublishSuccess"
          type="success"
          :message="newPublishSuccess"
          show-icon
          style="margin-top: 8px"
        />
        <a-alert
          v-if="platformOptions.length === 0 && !accountsLoading"
          type="info"
          :message="'未配置任何平台账号,请前往 /settings 配置'"
          show-icon
          style="margin-top: 8px"
        />
      </a-form>
    </a-modal>

    <!-- Preview 结果 Drawer（共用原 PublishRecords 的 PreviewResultPanel 模式） -->
    <a-drawer
      :open="previewDrawerOpen"
      title="Dry-run 发布预演"
      :width="520"
      @close="closePreviewDrawer"
    >
      <PublishPreviewPanel
        v-if="previewStore.lastResult"
        :result="previewStore.lastResult"
      />
      <a-empty v-else description="等待后端 run 完成后展示" />
    </a-drawer>
  </div>
</template>

<script lang="ts">
// 把 PreviewResultPanel 单独写成 defineComponent,保持和原 PublishRecords 一致
interface PanelProps { result: PreviewResult }

const PublishPreviewPanel = defineComponent({
  name: 'PublishPreviewPanel',
  props: { result: { type: Object as PropType<PreviewResult>, required: true } },
  setup(props: PanelProps) {
    return () => {
      const r = props.result
      const preview = r.preview
      return h('div', [
        h('a-alert', {
          type: r.validate_passed ? 'success' : 'warning',
          showIcon: true,
          style: 'margin-bottom: 12px',
          message: r.validate_passed
            ? '本地校验通过'
            : `本地校验有 ${r.validate_errors.length} 条问题`,
        }),
        r.validate_errors.length
          ? h('a-list', {
              size: 'small',
              dataSource: r.validate_errors,
              style: 'margin-bottom: 12px',
              renderItem: ({ item }: { item: string }) =>
                h('a-list-item', () => h('span', { style: 'color:#c41d7f' }, item)),
            })
          : null,
        h('a-descriptions', { title: '预览内容', bordered: true, size: 'small', column: 1 }, () => [
          h('a-descriptions-item', { label: '标题' }, () => preview.title),
          h('a-descriptions-item', { label: '正文摘要' },
            () => preview.body_excerpt || '（空）'),
          h('a-descriptions-item', { label: '平台' }, () => preview.platform),
          h('a-descriptions-item', { label: '账号' }, () => preview.account_id),
          h('a-descriptions-item', { label: '排期' }, () => preview.scheduled_at),
          h('a-descriptions-item', { label: '媒体' },
            () => preview.media.length
              ? h('a-list', { size: 'small', dataSource: preview.media },
                  {
                    renderItem: ({ item }: { item: string }) =>
                      h('a-list-item', () => h('code', item)),
                  })
              : '（无）'),
          h('a-descriptions-item', { label: 'tags' },
            () => preview.tags.length ? preview.tags.join(', ') : '（无）'),
        ]),
        h('a-alert', {
          type: 'info', showIcon: true, style: 'margin-top: 12px',
          message: `safe_publish: published=${r.safe_publish_result.published} dry_run=${r.safe_publish_result.dry_run}`,
        }, () => r.safe_publish_result.reason || '（无 reason）'),
      ])
    }
  },
})

export default PublishPreviewPanel
</script>
