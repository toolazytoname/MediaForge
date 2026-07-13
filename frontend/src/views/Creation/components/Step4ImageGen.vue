<script setup lang="ts">
// Step 4：真实 AI 出图（POST /api/v1/contents/{id}/generate-images）
// 失败时（如 image_provider_unavailable）显示 a-alert + 「前往设置」链接
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useImageGenStore, type ImageGenResult } from '../../../stores'

const props = defineProps<{
  contentId: string | null
}>()

const emit = defineEmits<{
  (e: 'done', result: ImageGenResult): void
}>()

const store = useImageGenStore()
const router = useRouter()
const localError = ref<string | null>(null)
const lastResult = ref<ImageGenResult | null>(null)

function splitError(err: string | null): { code: string; msg: string } {
  if (!err) return { code: 'unknown', msg: '' }
  const [code, ...rest] = err.split(':')
  return { code: (code ?? 'unknown').trim(), msg: rest.join(':').trim() }
}

const errorInfo = computed(() => splitError(localError.value))

async function onRun() {
  if (!props.contentId) return
  localError.value = null
  const r = await store.run(props.contentId)
  if (r) {
    lastResult.value = r
    emit('done', r)
  } else {
    localError.value = store.lastError ?? '未知错误'
  }
}

function goSettings() {
  router.push('/settings')
}

function fileUrl(p: string): string {
  if (p.startsWith('/')) return p
  return '/output/' + p.replace(/^output\//, '')
}
</script>

<template>
  <div>
    <a-empty v-if="!contentId" description="请先完成 Step 2 创建内容" />
    <a-form v-else layout="vertical">
      <a-form-item label="真实出 cover.png + 内联图（依赖 image provider key）">
        <a-tag color="purple">content_id = {{ contentId }}</a-tag>
      </a-form-item>
      <a-form-item>
        <a-button
          type="primary"
          size="large"
          :loading="store.running"
          :disabled="!contentId || store.running"
          @click="onRun"
        >
          ▶ 真实 AI 出图
        </a-button>
      </a-form-item>

      <a-alert
        v-if="lastResult"
        type="success"
        :message="`已出图：cover + ${lastResult.inline_images.length} inline · 成本 $${lastResult.cost_usd.toFixed(4)}`"
        show-icon
        closable
        style="margin-top: 12px"
        @close="lastResult = null"
      />

      <div v-if="lastResult" style="margin-top: 12px">
        <a-image v-if="lastResult.cover_path" :src="fileUrl(lastResult.cover_path)" :width="200" />
        <div
          v-if="lastResult.inline_images.length"
          style="margin-top: 8px; display: flex; gap: 8px; flex-wrap: wrap"
        >
          <a-image
            v-for="(u, i) in lastResult.inline_images"
            :key="i"
            :src="fileUrl(u)"
            :width="100"
          />
        </div>
      </div>

      <a-alert
        v-if="localError"
        type="error"
        show-icon
        closable
        style="margin-top: 12px"
        @close="localError = null"
      >
        <template #message>
          <span>出图失败: {{ errorInfo.code }} - {{ errorInfo.msg }}</span>
        </template>
        <template #description>
          <div v-if="errorInfo.code === 'image_provider_unavailable'">
            AI 出图需配置 image provider key（MINIMAX_IMAGE_API_KEY 或 MINIMAX_API_KEY 环境变量），详见
            <a-button size="small" type="link" @click="goSettings">前往设置 →</a-button>
          </div>
        </template>
      </a-alert>
    </a-form>
  </div>
</template>
