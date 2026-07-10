<script setup lang="ts">
// Step 2：创建内容（POST /api/v1/contents → 返回 content）
// 成功 → 向上 emit 'created'，父组件 auto-jump 到 Step 3
import { computed, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { useCreationStore, type ContentItem } from '../../../stores'

const props = defineProps<{
  topicId: string | null
}>()

const emit = defineEmits<{
  (e: 'created', content: ContentItem): void
}>()

const store = useCreationStore()
const { running, lastResult, lastError } = storeToRefs(store)
const localError = ref<string | null>(null)

const canCreate = computed(() => Boolean(props.topicId) && !running.value)

async function onCreate() {
  if (!props.topicId) return
  localError.value = null
  const r = await store.run(props.topicId)
  if (r) {
    emit('created', r)
  } else {
    localError.value = lastError.value ?? '未知错误'
  }
}

function splitError(err: string | null): { code: string; msg: string } {
  if (!err) return { code: 'unknown', msg: '' }
  const [code, ...rest] = err.split(':')
  return { code: (code ?? 'unknown').trim(), msg: rest.join(':').trim() }
}

const errorInfo = computed(() => splitError(localError.value))
</script>

<template>
  <a-spin :spinning="running" tip="创作中...（可能耗时数分钟）">
    <a-empty v-if="!topicId" description="请先到 Step 1 选定选题" />
    <a-form v-else layout="vertical">
      <a-form-item :label="`将基于选题 ${topicId} 创建 canonical 长文`">
        <a-tag color="purple">topic_id = {{ topicId }}</a-tag>
      </a-form-item>
      <a-form-item>
        <a-button
          type="primary"
          size="large"
          :loading="running"
          :disabled="!canCreate"
          @click="onCreate"
        >
          ▶ 一键创作
        </a-button>
      </a-form-item>
      <a-result
        v-if="lastResult"
        status="success"
        title="创作完成"
        :sub-title="`内容 ID: ${lastResult.id} · 状态: ${lastResult.status}`"
      />
      <a-alert
        v-if="localError"
        type="error"
        show-icon
        closable
        :message="`创作失败: ${errorInfo.code} - ${errorInfo.msg}`"
        @close="localError = null"
      />
    </a-form>
  </a-spin>
</template>
