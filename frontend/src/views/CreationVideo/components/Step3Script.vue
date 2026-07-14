<script setup lang="ts">
// Step 3：LLM 派生口播稿（可编辑）——POST /contents/{id}/video-script
import { computed, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { useVideoCreationStore } from '../../../stores'

const props = defineProps<{
  contentId: string | null
  durationS: number
}>()

const store = useVideoCreationStore()
const { script, running, lastError } = storeToRefs(store)
const localError = ref<string | null>(null)

function splitError(err: string | null): { code: string; msg: string } {
  if (!err) return { code: 'unknown', msg: '' }
  const [code, ...rest] = err.split(':')
  return { code: (code ?? 'unknown').trim(), msg: rest.join(':').trim() }
}

const errorInfo = computed(() => splitError(localError.value))

async function onDerive() {
  if (!props.contentId) return
  localError.value = null
  const r = await store.deriveScript(props.contentId, props.durationS)
  if (r === null) {
    localError.value = store.lastError ?? '未知错误'
  }
}
</script>

<template>
  <div>
    <a-empty v-if="!contentId" description="请先完成 Step 1 选择内容" />
    <a-form v-else layout="vertical">
      <a-form-item>
        <a-button
          type="primary"
          :loading="running"
          :disabled="running"
          @click="onDerive"
        >
          ▶ AI 生成口播稿
        </a-button>
        <span style="margin-left: 8px; color: #8c8c8c; font-size: 12px">
          按目标时长 {{ durationS }}s 估算文字量，生成后可手动编辑
        </span>
      </a-form-item>
      <a-form-item label="口播稿（可编辑）">
        <a-textarea
          v-model:value="script"
          :rows="10"
          placeholder="点击上方按钮生成，或直接手动输入口播稿"
        />
      </a-form-item>

      <a-alert
        v-if="localError"
        type="error"
        show-icon
        closable
        style="margin-top: 12px"
        @close="localError = null"
      >
        <template #message>
          <span>生成失败: {{ errorInfo.code }} - {{ errorInfo.msg }}</span>
        </template>
      </a-alert>
    </a-form>
  </div>
</template>
