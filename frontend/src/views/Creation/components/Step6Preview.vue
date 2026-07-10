<script setup lang="ts">
// Step 6：预演（POST /api/v1/publications/{id}/publish/preview）
// 弹 a-drawer 展示 validate + 预览 + 「未真发」警告
import { computed, ref } from 'vue'
import { storeToRefs } from 'pinia'
import {
  usePreviewStore,
  useSettingsStore,
  type PreviewRun,
} from '../../../stores'

const props = defineProps<{
  publicationId: string | null
}>()

const store = usePreviewStore()
const settingsStore = useSettingsStore()
const { lastResult, lastError, running } = storeToRefs(store)

const drawerOpen = ref(false)
const localError = ref<string | null>(null)

async function onPreview() {
  if (!props.publicationId) return
  localError.value = null
  const r = await store.run(props.publicationId)
  if (r && (r as PreviewRun).status === 'succeeded') {
    drawerOpen.value = true
  } else {
    localError.value = lastError.value ?? '预演失败'
  }
}

function closeDrawer() {
  drawerOpen.value = false
  store.reset()
}

function splitError(err: string | null): { code: string; msg: string } {
  if (!err) return { code: 'unknown', msg: '' }
  const [code, ...rest] = err.split(':')
  return { code: (code ?? 'unknown').trim(), msg: rest.join(':').trim() }
}

const errorInfo = computed(() => splitError(localError.value))

const publishDisabled = computed(() => {
  const cfg = settingsStore.config as { publish?: { enabled?: boolean } } | null
  return cfg?.publish?.enabled === false
})
</script>

<template>
  <div>
    <a-empty v-if="!publicationId" description="请先完成 Step 5 排期" />
    <a-form v-else layout="vertical">
      <a-form-item label="dry-run 发布预演（不会真发）">
        <a-tag color="purple">publication_id = {{ publicationId }}</a-tag>
      </a-form-item>
      <a-form-item>
        <a-button type="primary" size="large" :loading="running" :disabled="running" @click="onPreview">
          🔍 预演发布
        </a-button>
      </a-form-item>
      <a-alert v-if="publishDisabled" type="warning" show-icon style="margin-top: 12px"
        message="publish.enabled=false：safe_publish 会以「publish is disabled」拒绝（不会真发）。" />
      <a-alert v-if="localError" type="error" :message="`预演失败: ${errorInfo.code} - ${errorInfo.msg}`"
        show-icon closable style="margin-top: 12px" @close="localError = null" />
      <a-alert type="warning" show-icon style="margin-top: 16px"
        message="⚠️ 这是预演，未实际发布。要真正发布请走 CLI python -m pipeline.run publish" />
    </a-form>

    <a-drawer :open="drawerOpen" title="Dry-run 发布预演" :width="560" @close="closeDrawer">
      <a-spin :spinning="running">
        <a-empty v-if="!lastResult" description="等待后端 run 完成后展示" />
        <template v-else>
          <a-alert :type="lastResult.validate_passed ? 'success' : 'warning'" show-icon
            style="margin-bottom: 12px"
            :message="lastResult.validate_passed ? '本地校验通过'
              : `本地校验有 ${lastResult.validate_errors.length} 条问题`" />
          <a-list v-if="lastResult.validate_errors.length" size="small"
            :data-source="lastResult.validate_errors" style="margin-bottom: 12px">
            <template #renderItem="{ item }">
              <a-list-item><span style="color: #c41d7f">{{ item }}</span></a-list-item>
            </template>
          </a-list>
          <a-descriptions title="预览内容" bordered size="small" :column="1">
            <a-descriptions-item label="标题">{{ lastResult.preview.title }}</a-descriptions-item>
            <a-descriptions-item label="正文摘要">{{ lastResult.preview.body_excerpt || '（空）' }}</a-descriptions-item>
            <a-descriptions-item label="平台">{{ lastResult.preview.platform }}</a-descriptions-item>
            <a-descriptions-item label="账号">{{ lastResult.preview.account_id }}</a-descriptions-item>
            <a-descriptions-item label="排期">{{ lastResult.preview.scheduled_at }}</a-descriptions-item>
            <a-descriptions-item label="媒体">
              <a-list v-if="lastResult.preview.media.length" size="small" :data-source="lastResult.preview.media">
                <template #renderItem="{ item }">
                  <a-list-item><code style="font-size: 11px">{{ item }}</code></a-list-item>
                </template>
              </a-list>
              <span v-else>（无）</span>
            </a-descriptions-item>
            <a-descriptions-item label="tags">
              {{ lastResult.preview.tags.length ? lastResult.preview.tags.join(', ') : '（无）' }}
            </a-descriptions-item>
          </a-descriptions>
          <a-alert type="info" show-icon style="margin-top: 12px"
            :message="`safe_publish: published=${lastResult.safe_publish_result.published} dry_run=${lastResult.safe_publish_result.dry_run}`" />
        </template>
      </a-spin>
    </a-drawer>
  </div>
</template>
