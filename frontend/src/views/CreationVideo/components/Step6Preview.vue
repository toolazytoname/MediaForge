<script setup lang="ts">
// Step 6：预览生成结果——直接播放 job.output_url 指向的真实视频文件
import { computed } from 'vue'
import { storeToRefs } from 'pinia'
import { useVideoCreationStore } from '../../../stores'

const store = useVideoCreationStore()
const { job } = storeToRefs(store)

const isDone = computed(() => job.value?.state === 'done' && !!job.value?.output_url)
const isFailed = computed(() => job.value?.state === 'failed')
</script>

<template>
  <div>
    <a-empty v-if="!job" description="请先完成 Step 5 提交并等待任务完成" />
    <a-alert
      v-else-if="isFailed"
      type="error"
      :message="`生成失败: ${job?.error ?? '未知错误'}`"
      show-icon
    />
    <a-empty v-else-if="!isDone" description="任务尚未完成，请回到 Step 5 等待轮询结束" />
    <div v-else>
      <video
        controls
        :src="job!.output_url!"
        style="max-width: 100%; max-height: 480px; background: #000"
      />
      <p style="margin-top: 8px; color: #8c8c8c; font-size: 12px">
        文件路径：<code>{{ job!.output_path }}</code>
      </p>
    </div>
  </div>
</template>
