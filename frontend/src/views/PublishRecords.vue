<script setup lang="ts">
// M10-8 PublishRecords：发布记录列表（含可选 metric）
// M10-12 阶段 E：行内加「🔍 预演」按钮（仅 queued 可见）
//   - 点击 → POST /api/v1/publications/{id}/publish/preview
//   - 后台 run 完成后 a-drawer 展示 validate/preview/safe_publish_result
//   - 失败 a-alert 展示 reason
import { computed, onMounted, ref } from 'vue'
import {
  usePublishStore,
  usePreviewStore,
  useSettingsStore,
  type PreviewResult,
} from '../stores'
import { storeToRefs } from 'pinia'
import { formatDateTime } from '../utils/format'

const store = usePublishStore()
const previewStore = usePreviewStore()
const settingsStore = useSettingsStore()
const { records, loading } = storeToRefs(store)
const filters = ref<{ status?: string; platform?: string; with_metric?: boolean }>({ with_metric: true })

const drawerOpen = ref(false)
const previewRunning = ref<string | null>(null)
const previewError = ref<string | null>(null)

function reload() {
  store.loadRecords({ ...filters.value })
}
onMounted(async () => {
  if (!settingsStore.config) {
    await settingsStore.load()
  }
  reload()
})

const publishEnabled = computed(() => {
  const cfg = settingsStore.config as { publish?: { enabled?: boolean } } | null
  return cfg?.publish?.enabled !== false
})

async function onPreview(publicationId: string) {
  previewError.value = null
  previewRunning.value = publicationId
  try {
    const result = await previewStore.run(publicationId)
    if (!result) {
      previewError.value = previewStore.lastError ?? '预演失败'
      return
    }
    drawerOpen.value = true
  } finally {
    previewRunning.value = null
  }
}

function isQueued(item: { status: string }): boolean {
  return item.status === 'queued'
}

function closeDrawer() {
  drawerOpen.value = false
  previewStore.reset()
}
</script>

<template>
  <h2>发布记录</h2>
  <a-space style="margin-bottom: 12px">
    <a-select v-model:value="filters.status" placeholder="status" allow-clear style="width: 140px" @change="reload">
      <a-select-option value="queued">queued</a-select-option>
      <a-select-option value="publishing">publishing</a-select-option>
      <a-select-option value="published">published</a-select-option>
      <a-select-option value="failed">failed</a-select-option>
      <a-select-option value="cancelled">cancelled</a-select-option>
    </a-select>
    <a-input v-model:value="filters.platform" placeholder="platform" allow-clear style="width: 140px" @press-enter="reload" />
    <a-checkbox v-model:checked="filters.with_metric" @change="reload">含最新 metric</a-checkbox>
    <a-button @click="reload">刷新</a-button>
  </a-space>
  <a-alert
    v-if="!publishEnabled"
    type="warning"
    show-icon
    style="margin-bottom: 12px"
    message="publish.enabled=false：🔍 预演按钮只读展示，safe_publish 会以「publish is disabled」拒绝（不会真发）。"
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
    <a-table
      :data-source="records"
      :columns="[
        { title: 'id', dataIndex: 'id', width: 120 },
        { title: 'platform', dataIndex: 'platform', width: 100 },
        { title: 'account', dataIndex: 'account_id', width: 100 },
        { title: 'scheduled_at', dataIndex: 'scheduled_at', width: 200 },
        { title: 'status', dataIndex: 'status', width: 100 },
        { title: 'views', key: 'views', width: 80 },
        { title: 'likes', key: 'likes', width: 80 },
        { title: 'error', dataIndex: 'error' },
        { title: 'actions', key: 'actions', width: 110 },
      ]"
      :pagination="{ pageSize: 50 }"
      row-key="id"
      size="small"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.dataIndex === 'scheduled_at'">
          {{ formatDateTime(record.scheduled_at) }}
        </template>
        <template v-else-if="column.key === 'views'">
          {{ record.latest_metric?.views ?? '—' }}
        </template>
        <template v-else-if="column.key === 'likes'">
          {{ record.latest_metric?.likes ?? '—' }}
        </template>
        <template v-else-if="column.key === 'actions'">
          <a-button
            size="small"
            :disabled="!isQueued(record) || previewStore.running"
            :loading="previewRunning === record.id"
            @click="onPreview(record.id)"
          >
            🔍 预演
          </a-button>
        </template>
      </template>
    </a-table>
  </a-spin>

  <a-drawer
    :open="drawerOpen"
    title="Dry-run 发布预演"
    :width="520"
    @close="closeDrawer"
  >
    <PreviewResultPanel
      v-if="previewStore.lastResult"
      :result="previewStore.lastResult"
    />
    <a-empty v-else description="等待后端 run 完成后展示" />
  </a-drawer>
</template>

<script lang="ts">
import { defineComponent, h, type PropType } from 'vue'

interface BodyProps {
  result: PreviewResult
}

const PreviewResultPanel = defineComponent({
  name: 'PreviewResultPanel',
  props: { result: { type: Object as PropType<PreviewResult>, required: true } },
  setup(props: BodyProps) {
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
