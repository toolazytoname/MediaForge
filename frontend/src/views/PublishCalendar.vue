<script setup lang="ts">
// M10-8 PublishCalendar：周视图日历（按日期分桶）
// M10 P2 阶段 C：reschedule / cancel / retry 按钮解 disabled
//   - reschedule: 弹 a-modal 改 scheduled_at → POST /api/v1/publications/{id}/reschedule
//   - cancel: POST /api/v1/publications/{id}/cancel
//   - retry: POST /api/v1/publications/{id}/retry
// M10-12 阶段 E：每条 queued 出版物加「🔍 预演」按钮 + a-drawer
import { computed, onMounted, ref } from 'vue'
import {
  usePublishStore,
  usePubActionStore,
  usePreviewStore,
  useSettingsStore,
  type PreviewResult,
} from '../stores'
import { storeToRefs } from 'pinia'

const store = usePublishStore()
const actionStore = usePubActionStore()
const previewStore = usePreviewStore()
const settingsStore = useSettingsStore()
const { calendar, loading } = storeToRefs(store)

const week = ref<string | undefined>(undefined)

const success = ref<string | null>(null)
const errorAlert = ref<{ code: string; msg: string } | null>(null)

// reschedule modal state
const modalOpen = ref(false)
const modalPubId = ref<string | null>(null)
const modalNewTime = ref<string>('2026-07-12T18:30:00+00:00')

// preview state
const previewDrawerOpen = ref(false)
const previewRunning = ref<string | null>(null)
const previewError = ref<string | null>(null)

function load() {
  store.loadCalendar(week.value)
}
onMounted(async () => {
  if (!settingsStore.config) {
    await settingsStore.load()
  }
  load()
})

const publishEnabled = computed(() => {
  const cfg = settingsStore.config as { publish?: { enabled?: boolean } } | null
  return cfg?.publish?.enabled !== false
})

async function onCancel(pubId: string) {
  success.value = null
  errorAlert.value = null
  const r = await actionStore.cancel(pubId)
  if (r) {
    success.value = `已 cancel: ${r.id} → ${r.status}`
    load()
  } else {
    showError()
  }
}

async function onRetry(pubId: string) {
  success.value = null
  errorAlert.value = null
  const r = await actionStore.retry(pubId)
  if (r) {
    success.value = `已 retry: ${r.id} → ${r.status}`
    load()
  } else {
    showError()
  }
}

function openReschedule(pubId: string, currentTime: string) {
  modalPubId.value = pubId
  modalNewTime.value = currentTime
  modalOpen.value = true
}

async function onRescheduleSubmit() {
  if (!modalPubId.value) return
  const pubId = modalPubId.value
  success.value = null
  errorAlert.value = null
  const r = await actionStore.reschedule(pubId, modalNewTime.value)
  modalOpen.value = false
  modalPubId.value = null
  if (r) {
    success.value = `已 reschedule: ${r.id} → ${r.scheduled_at}`
    load()
  } else {
    showError()
  }
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

function closePreview() {
  previewDrawerOpen.value = false
  previewStore.reset()
}

function showError() {
  const [code, ...rest] = (actionStore.lastError ?? '').split(':')
  errorAlert.value = {
    code: code ?? 'unknown',
    msg: rest.join(':').trim(),
  }
}

function isQueued(item: any): boolean {
  return item.status === 'queued'
}
function isFailed(item: any): boolean {
  return item.status === 'failed'
}
</script>

<template>
  <h2>发布日历</h2>
  <a-space style="margin-bottom: 12px">
    <a-input v-model:value="week" placeholder="YYYY-MM-DD（可选）" allow-clear style="width: 200px"
             @press-enter="load" />
    <a-button @click="load">加载</a-button>
  </a-space>
  <a-alert
    v-if="!publishEnabled"
    type="warning"
    show-icon
    style="margin-bottom: 12px"
    message="publish.enabled=false：🔍 预演按钮只读展示，safe_publish 会以「publish is disabled」拒绝（不会真发）。"
  />
  <a-alert
    v-if="success"
    type="success"
    :message="success"
    show-icon
    closable
    style="margin-bottom: 12px"
    @close="success = null"
  />
  <a-alert
    v-if="errorAlert"
    type="error"
    :message="`操作失败: ${errorAlert.code} - ${errorAlert.msg}`"
    show-icon
    closable
    style="margin-bottom: 12px"
    @close="errorAlert = null"
  />
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
    <template v-if="calendar">
      <p>
        <a :href="`/publish/calendar?week=${calendar.prev_week}`">← 上周 ({{ calendar.prev_week }})</a>
        | 本周：{{ calendar.week_start }} → {{ calendar.week_end }}
        | <a :href="`/publish/calendar?week=${calendar.next_week}`">下周 ({{ calendar.next_week }}) →</a>
      </p>
      <a-row :gutter="8">
        <a-col v-for="d in calendar.days" :key="d.date" :span="3">
          <a-card :title="d.date" size="small" style="margin-bottom: 8px">
            <a-list size="small" :data-source="d.publications" :pagination="{ pageSize: 5 }">
              <template #renderItem="{ item }">
                <a-list-item>
                  <a-tag color="purple">{{ item.platform }}</a-tag>
                  <span style="font-size: 12px">{{ item.scheduled_at.split('T')[1]?.slice(0,5) }}</span>
                  <a-tag :color="item.status === 'published' ? 'green' : 'orange'">{{ item.status }}</a-tag>
                  <div v-if="isQueued(item) || isFailed(item)" style="margin-top: 4px">
                    <a-button
                      v-if="isQueued(item)"
                      size="small"
                      :loading="actionStore.running"
                      @click="openReschedule(item.id, item.scheduled_at)"
                    >
                      reschedule
                    </a-button>
                    <a-button
                      v-if="isQueued(item)"
                      size="small"
                      danger
                      :loading="actionStore.running"
                      style="margin-left: 4px"
                      @click="onCancel(item.id)"
                    >
                      cancel
                    </a-button>
                    <a-button
                      v-if="isQueued(item)"
                      size="small"
                      :loading="previewRunning === item.id || previewStore.running"
                      style="margin-left: 4px"
                      @click="onPreview(item.id)"
                    >
                      🔍 预演
                    </a-button>
                    <a-button
                      v-if="isFailed(item)"
                      size="small"
                      type="primary"
                      :loading="actionStore.running"
                      @click="onRetry(item.id)"
                    >
                      retry
                    </a-button>
                  </div>
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

  <a-modal
    v-model:open="modalOpen"
    title="改排期时间"
    @ok="onRescheduleSubmit"
    :ok-button-props="{ loading: actionStore.running }"
  >
    <a-form layout="vertical">
      <a-form-item label="新的 scheduled_at (ISO8601 UTC)">
        <a-input v-model:value="modalNewTime" placeholder="2026-07-12T18:30:00+00:00" />
      </a-form-item>
      <p style="color: #888; font-size: 12px">
        publication: {{ modalPubId }}
      </p>
    </a-form>
  </a-modal>

  <a-drawer
    :open="previewDrawerOpen"
    title="Dry-run 发布预演"
    :width="520"
    @close="closePreview"
  >
    <PreviewResultPanel
      v-if="previewStore.lastResult"
      :result="previewStore.lastResult as PreviewResult"
    />
    <a-empty v-else description="等待后端 run 完成后展示" />
  </a-drawer>
</template>

<script lang="ts">
import { defineComponent, h, type PropType } from 'vue'

interface PanelProps {
  result: PreviewResult
}

const PreviewResultPanel = defineComponent({
  name: 'PreviewResultPanel',
  props: { result: { type: Object as PropType<PreviewResult>, required: true } },
  setup(props: PanelProps) {
    return () => {
      const r = props.result
      const preview = r.preview
      return h('div', [
        h(
          'a-alert',
          {
            type: r.validate_passed ? 'success' : 'warning',
            showIcon: true,
            style: 'margin-bottom: 12px',
            message: r.validate_passed
              ? '本地校验通过'
              : `本地校验有 ${r.validate_errors.length} 条问题`,
          },
        ),
        r.validate_errors.length
          ? h(
              'a-list',
              {
                size: 'small',
                dataSource: r.validate_errors,
                style: 'margin-bottom: 12px',
              },
              {
                renderItem: ({ item }: { item: string }) =>
                  h('a-list-item', () => h('span', { style: 'color:#c41d7f' }, item)),
              },
            )
          : null,
        h(
          'a-descriptions',
          { title: '预览内容', bordered: true, size: 'small', column: 1 },
          () => [
            h('a-descriptions-item', { label: '标题' }, () => preview.title),
            h(
              'a-descriptions-item',
              { label: '正文摘要' },
              () => preview.body_excerpt || '（空）',
            ),
            h('a-descriptions-item', { label: '平台' }, () => preview.platform),
            h('a-descriptions-item', { label: '账号' }, () => preview.account_id),
            h(
              'a-descriptions-item',
              { label: '排期' },
              () => preview.scheduled_at,
            ),
            h(
              'a-descriptions-item',
              { label: '媒体' },
              () => preview.media.length
                ? h(
                    'a-list',
                    { size: 'small', dataSource: preview.media },
                    {
                      renderItem: ({ item }: { item: string }) =>
                        h('a-list-item', () => h('code', item)),
                    },
                  )
                : '（无）',
            ),
            h(
              'a-descriptions-item',
              { label: 'tags' },
              () => preview.tags.length ? preview.tags.join(', ') : '（无）',
            ),
          ],
        ),
        h(
          'a-alert',
          {
            type: 'info',
            showIcon: true,
            style: 'margin-top: 12px',
            message: `safe_publish: published=${r.safe_publish_result.published} dry_run=${r.safe_publish_result.dry_run}`,
          },
          () => r.safe_publish_result.reason || '（无 reason）',
        ),
      ])
    }
  },
})

export default PreviewResultPanel
</script>
