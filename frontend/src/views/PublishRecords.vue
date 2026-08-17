<script setup lang="ts">
// M10-8 PublishRecords：发布记录列表（含可选 metric）
// M10-12 阶段 E：行内加「🔍 预演」按钮（仅 queued 可见）
//   - 点击 → POST /api/v1/publications/{id}/publish/preview
//   - 后台 run 完成后 a-drawer 展示 validate/preview/safe_publish_result
//   - 失败 a-alert 展示 reason
// M10 Phase D：行内加「🚀 立即发布」按钮（仅 queued 且未被 config 门禁禁用可见）
//   - 点击 → 先跑一遍预演展示结果 → Modal.confirm 二次确认（危险操作）
//   - 确认后 → POST /api/v1/publications/{id}/publish（真实 safe_publish dry_run=False）
//   - 仍完整强制 safe_publish 三重锁 + config.publish.enabled/allowed_platforms 门禁
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Modal } from 'ant-design-vue'
import {
  usePublishStore,
  usePreviewStore,
  useRealPublishStore,
  useSettingsStore,
  useCapabilitiesStore,
  type PreviewResult,
  type RealPublishResult,
} from '../stores'
import { storeToRefs } from 'pinia'
import { formatDateTime } from '../utils/format'

const route = useRoute()
const router = useRouter()
const store = usePublishStore()
const previewStore = usePreviewStore()
const realPublishStore = useRealPublishStore()
const settingsStore = useSettingsStore()
const capabilitiesStore = useCapabilitiesStore()
const { records, loading } = storeToRefs(store)
const filters = ref<{ status?: string; platform?: string; content_id?: string; with_metric?: boolean }>({
  with_metric: true,
  content_id: typeof route.query.content_id === 'string' ? route.query.content_id : undefined,
})

const drawerOpen = ref(false)
const drawerMode = ref<'preview' | 'publish'>('preview')
const previewRunning = ref<string | null>(null)
const previewError = ref<string | null>(null)
const realPublishRunning = ref<string | null>(null)
const realPublishError = ref<string | null>(null)

function reload() {
  store.loadRecords({ ...filters.value })
}
function clearContentFilter() {
  filters.value = { ...filters.value, content_id: undefined }
  router.replace({ query: { ...route.query, content_id: undefined } })
  reload()
}
onMounted(async () => {
  if (!settingsStore.config) {
    await settingsStore.load()
  }
  await capabilitiesStore.load()
  reload()
})

const publishEnabled = computed(() => {
  const cfg = settingsStore.config as { publish?: { enabled?: boolean } } | null
  return cfg?.publish?.enabled !== false
})

const allowedPlatforms = computed(() => {
  const cfg = settingsStore.config as { publish?: { allowed_platforms?: string[] } } | null
  return cfg?.publish?.allowed_platforms ?? null
})

function isPlatformAllowed(record: { platform: string }): boolean {
  const allowed = allowedPlatforms.value
  return allowed === null || allowed.includes(record.platform)
}

function canRealPublish(record: { status: string; platform: string }): boolean {
  return isQueued(record) && publishEnabled.value && isPlatformAllowed(record)
}

async function onPreview(publicationId: string) {
  previewError.value = null
  previewRunning.value = publicationId
  try {
    const result = await previewStore.run(publicationId)
    if (!result) {
      previewError.value = previewStore.lastError ?? '预演失败'
      return
    }
    drawerMode.value = 'preview'
    drawerOpen.value = true
  } finally {
    previewRunning.value = null
  }
}

async function onRealPublish(record: { id: string; platform: string }) {
  realPublishError.value = null
  previewError.value = null
  // 发布前始终重新跑一遍预演，保证确认弹条里展示的是最新状态
  previewRunning.value = record.id
  let previewOk = false
  try {
    const previewResult = await previewStore.run(record.id)
    previewOk = !!previewResult && !previewStore.lastError
    if (!previewOk) {
      previewError.value = previewStore.lastError ?? '预演失败'
      return
    }
    drawerMode.value = 'preview'
    drawerOpen.value = true
  } finally {
    previewRunning.value = null
  }
  if (!previewOk) {
    return
  }

  const capability = capabilitiesStore.forPlatform(record.platform)
  Modal.confirm({
    title: '确认真实发布',
    content: capability?.ui.confirm_copy ?? `将提交到 ${capability?.label ?? record.platform}。此操作不可撤销。`,
    okText: '确定发布',
    okType: 'danger',
    cancelText: '取消',
    onOk: async () => {
      realPublishRunning.value = record.id
      try {
        const result = await realPublishStore.run(record.id)
        if (!result) {
          realPublishError.value = realPublishStore.lastError ?? '发布失败'
          return
        }
        drawerMode.value = 'publish'
        drawerOpen.value = true
        reload()
      } finally {
        realPublishRunning.value = null
      }
    },
  })
}

function isQueued(item: { status: string }): boolean {
  return item.status === 'queued'
}

function closeDrawer() {
  drawerOpen.value = false
  previewStore.reset()
  realPublishStore.reset()
}
</script>

<template>
  <h2>发布记录</h2>
  <a-alert
    v-if="filters.content_id"
    type="info"
    show-icon
    closable
    style="margin-bottom: 12px"
    :message="`已按内容过滤：${filters.content_id}`"
    @close="clearContentFilter"
  />
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
    message="publish.enabled=false：🔍 预演按钮只读展示，🚀 立即发布按钮禁用，safe_publish 会以「publish is disabled」拒绝（不会真发）。"
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
  <a-alert
    v-if="realPublishError"
    type="error"
    show-icon
    closable
    style="margin-bottom: 12px"
    :message="`发布失败：${realPublishError}`"
    @close="realPublishError = null"
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
        { title: 'actions', key: 'actions', width: 200 },
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
          <a-space size="small">
            <a-button
              size="small"
              :disabled="!isQueued(record) || previewStore.running"
              :loading="previewRunning === record.id"
              @click="onPreview(record.id)"
            >
              🔍 预演
            </a-button>
            <a-tooltip
              :title="!isQueued(record)
                ? '仅 queued 状态可发布'
                : !publishEnabled
                  ? 'config.publish.enabled=false'
                  : !isPlatformAllowed(record)
                    ? `${record.platform} 不在 allowed_platforms 白名单`
                    : ''"
            >
              <a-button
                size="small"
                danger
                :disabled="!canRealPublish(record) || realPublishStore.running"
                :loading="realPublishRunning === record.id"
                @click="onRealPublish(record)"
              >
                🚀 立即发布
              </a-button>
            </a-tooltip>
          </a-space>
        </template>
      </template>
    </a-table>
  </a-spin>

  <a-drawer
    :open="drawerOpen"
    :title="drawerMode === 'publish' ? '真实发布结果' : 'Dry-run 发布预演'"
    :width="520"
    @close="closeDrawer"
  >
    <RealPublishResultPanel
      v-if="drawerMode === 'publish' && realPublishStore.lastResult"
      :result="realPublishStore.lastResult"
    />
    <PreviewResultPanel
      v-else-if="previewStore.lastResult"
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

interface RealPublishBodyProps {
  result: RealPublishResult
}

const RealPublishResultPanel = defineComponent({
  name: 'RealPublishResultPanel',
  props: { result: { type: Object as PropType<RealPublishResult>, required: true } },
  setup(props: RealPublishBodyProps) {
    return () => {
      const r = props.result
      return h('div', [
        h(
          'a-alert',
          {
            type: r.published ? 'success' : 'error',
            showIcon: true,
            style: 'margin-bottom: 12px',
            message: r.published ? '发布成功' : '发布未执行',
          },
          () => r.reason || '（无 reason）',
        ),
        r.published
          ? h(
              'a-descriptions',
              { title: '发布结果', bordered: true, size: 'small', column: 1 },
              () => [
                h(
                  'a-descriptions-item',
                  { label: 'platform_post_id' },
                  () => r.platform_post_id ?? '（无）',
                ),
                h(
                  'a-descriptions-item',
                  { label: '链接' },
                  () =>
                    r.url
                      ? h(
                          'a',
                          { href: r.url, target: '_blank', rel: 'noopener' },
                          r.url,
                        )
                      : '（无）',
                ),
              ],
            )
          : null,
      ])
    }
  },
})

export default PreviewResultPanel
export { RealPublishResultPanel }
</script>
