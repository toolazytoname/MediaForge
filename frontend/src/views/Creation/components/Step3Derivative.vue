<script setup lang="ts">
// Step 3：衍生小红书（POST /api/v1/contents/{id}/derivative）
// 完成标志：emit done 后父组件把 result 写进 wizard.derivative
import { computed, ref } from 'vue'
import { useDerivativeStore, type DerivativeResult } from '../../../stores'

const props = defineProps<{
  contentId: string | null
}>()

const emit = defineEmits<{
  (e: 'done', result: DerivativeResult): void
}>()

const store = useDerivativeStore()
const localError = ref<string | null>(null)
const successMsg = ref<string | null>(null)

function splitError(err: string | null): { code: string; msg: string } {
  if (!err) return { code: 'unknown', msg: '' }
  const [code, ...rest] = err.split(':')
  return { code: (code ?? 'unknown').trim(), msg: rest.join(':').trim() }
}

const errorInfo = computed(() => splitError(localError.value))

async function onDerive() {
  if (!props.contentId) return
  localError.value = null
  successMsg.value = null
  const r = await store.run(props.contentId)
  if (r) {
    successMsg.value = `已衍生小红书：${r.slides_count} 张 slides · caption ${r.caption_chars} 字 · ${r.tags.length} 个 tags`
    emit('done', r)
  } else {
    localError.value = store.lastError ?? '未知错误'
  }
}
</script>

<template>
  <div>
    <a-empty v-if="!contentId" description="请先完成 Step 2 创建内容" />
    <a-form v-else layout="vertical">
      <a-form-item label="将基于 canonical.md 生成小红书 slides + caption + tags">
        <a-tag color="purple">content_id = {{ contentId }}</a-tag>
      </a-form-item>
      <a-form-item>
        <a-button
          type="primary"
          size="large"
          :loading="store.running"
          :disabled="!contentId || store.running"
          @click="onDerive"
        >
          ▶ 衍生小红书
        </a-button>
      </a-form-item>
      <a-alert
        v-if="successMsg"
        type="success"
        :message="successMsg"
        show-icon
        closable
        style="margin-top: 12px"
        @close="successMsg = null"
      />
      <a-alert
        v-if="localError"
        type="error"
        :message="`衍生失败: ${errorInfo.code} - ${errorInfo.msg}`"
        show-icon
        closable
        style="margin-top: 12px"
        @close="localError = null"
      />
    </a-form>
  </div>
</template>
