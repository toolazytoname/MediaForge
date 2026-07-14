<script setup lang="ts">
// Step 2：选创作类型（打开 VideoTypeSelectModal 三选一）
import { ref } from 'vue'
import VideoTypeSelectModal from '../../../layouts/components/VideoTypeSelectModal.vue'
import type { VideoEngineName } from '../../../stores'

const ENGINE_LABELS: Record<VideoEngineName, string> = {
  mpt: '素材混剪',
  pixelle: 'AI 生成视频',
  digitalhuman: '数字人口播',
}

const props = defineProps<{
  engine: VideoEngineName | null
}>()

const emit = defineEmits<{
  (e: 'update:engine', v: VideoEngineName): void
}>()

const modalOpen = ref(false)

function openModal() {
  modalOpen.value = true
}

function onSelect(engine: VideoEngineName) {
  emit('update:engine', engine)
}
</script>

<template>
  <div>
    <a-empty v-if="!props.engine" description="尚未选择创作类型">
      <a-button type="primary" @click="openModal">选择创作类型</a-button>
    </a-empty>
    <div v-else>
      <a-descriptions :column="1" size="small" bordered>
        <a-descriptions-item label="已选类型">
          <a-tag color="purple">{{ ENGINE_LABELS[props.engine] }}</a-tag>
        </a-descriptions-item>
      </a-descriptions>
      <a-button style="margin-top: 12px" @click="openModal">重新选择</a-button>
    </div>

    <VideoTypeSelectModal
      v-model:open="modalOpen"
      @select="onSelect"
    />
  </div>
</template>
