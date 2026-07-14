<script setup lang="ts">
// Step 5：提交任务 + 轮询——只展示引擎真实回传的 state/progress/error 文字，
// 不伪造百分比进度条（CLAUDE.md 红线：不得杜撰/篡改 progress 语义）
import { computed, onUnmounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useVideoCreationStore, type VideoJobResult } from '../../../stores'

const props = defineProps<{
  contentId: string | null
  durationS: number
}>()

const emit = defineEmits<{
  (e: 'done', job: VideoJobResult): void
}>()

const store = useVideoCreationStore()
const { job, running, polling, lastError } = storeToRefs(store)

const STATE_LABELS: Record<string, string> = {
  submitted: '已提交，等待引擎处理',
  running: '引擎处理中',
  done: '已完成',
  failed: '失败',
}

// 提交发起时间戳（本地展示已耗时，纯前端计时，不涉及引擎 progress 语义）
const submittedAtMs = ref<number | null>(null)
const nowMs = ref(Date.now())
let tickHandle: ReturnType<typeof setInterval> | null = null

function startClock() {
  if (tickHandle !== null) return
  tickHandle = setInterval(() => {
    nowMs.value = Date.now()
  }, 1000)
}
function stopClock() {
  if (tickHandle !== null) {
    clearInterval(tickHandle)
    tickHandle = null
  }
}
onUnmounted(stopClock)

const elapsedLabel = computed(() => {
  if (submittedAtMs.value === null) return ''
  const seconds = Math.max(0, Math.round((nowMs.value - submittedAtMs.value) / 1000))
  return `已耗时 ${seconds}s`
})

watch(
  () => job.value?.state,
  (state) => {
    if (state === 'done' || state === 'failed') stopClock()
  },
)

watch(
  () => job.value,
  (j) => {
    if (j && (j.state === 'done')) emit('done', j)
  },
)

async function onSubmit() {
  if (!props.contentId) return
  const r = await store.submit(props.contentId, props.durationS)
  if (r) {
    submittedAtMs.value = Date.now()
    startClock()
    store.startPolling()
  }
}
</script>

<template>
  <div>
    <a-empty v-if="!contentId" description="请先完成前面步骤" />
    <a-form v-else layout="vertical">
      <a-form-item>
        <a-button
          type="primary"
          :loading="running"
          :disabled="running || polling"
          @click="onSubmit"
        >
          ▶ 提交视频生成任务
        </a-button>
      </a-form-item>

      <a-descriptions v-if="job" :column="1" size="small" bordered style="margin-top: 12px">
        <a-descriptions-item label="job_id">
          <code>{{ job.job_id }}</code>
        </a-descriptions-item>
        <a-descriptions-item label="状态">
          <a-tag
            :color="job.state === 'done' ? 'success' : job.state === 'failed' ? 'error' : 'processing'"
          >
            {{ STATE_LABELS[job.state] ?? job.state }}
          </a-tag>
          <span v-if="elapsedLabel" style="margin-left: 8px; color: #8c8c8c">{{ elapsedLabel }}</span>
        </a-descriptions-item>
        <a-descriptions-item v-if="job.progress !== null" label="引擎回传 progress">
          {{ job.progress }}
        </a-descriptions-item>
        <a-descriptions-item v-if="job.error" label="错误">
          <span style="color: #ff4d4f">{{ job.error }}</span>
        </a-descriptions-item>
      </a-descriptions>

      <a-alert
        v-if="lastError"
        type="error"
        :message="`提交失败: ${lastError}`"
        show-icon
        closable
        style="margin-top: 12px"
        @close="store.lastError = null"
      />
    </a-form>
  </div>
</template>
