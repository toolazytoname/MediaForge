<script setup lang="ts">
// M12-2：新建创作 类型选择弹窗（仿蚁小二「选择创作类型」卡片交互）
// 只放 3 张真实可用的卡片，不做未实现功能的占位卡（CLAUDE.md 红线）：
//   素材混剪(mpt) / AI 生成视频(pixelle) / 数字人口播(digitalhuman)
import type { VideoEngineName } from '../../stores'

interface Props {
  open: boolean
}

interface VideoTypeOption {
  engine: VideoEngineName
  title: string
  desc: string
}

defineProps<Props>()
const emit = defineEmits<{
  (e: 'update:open', value: boolean): void
  (e: 'select', engine: VideoEngineName): void
}>()

const options: ReadonlyArray<VideoTypeOption> = [
  { engine: 'mpt', title: '素材混剪', desc: '按脚本自动配素材 + 字幕，快速产出图文向视频' },
  { engine: 'pixelle', title: 'AI 生成视频', desc: '按脚本逐镜头 AI 生成画面，适合原创感强的内容' },
  { engine: 'digitalhuman', title: '数字人口播', desc: '真人形象循环视频 + TTS 语音，唇形同步生成口播视频' },
]

function close(value: boolean): void {
  emit('update:open', value)
}

function onSelect(opt: VideoTypeOption): void {
  emit('select', opt.engine)
  close(false)
}
</script>

<template>
  <a-modal
    :open="open"
    title="选择创作类型"
    :footer="null"
    width="640px"
    @update:open="close"
  >
    <div class="card-grid">
      <div
        v-for="opt in options"
        :key="opt.engine"
        class="type-card"
        @click="onSelect(opt)"
      >
        <h3 class="card-title">{{ opt.title }}</h3>
        <p class="card-desc">{{ opt.desc }}</p>
        <a-button type="primary" class="card-btn">选择</a-button>
      </div>
    </div>
  </a-modal>
</template>

<style scoped>
.card-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
  padding: 8px 0;
}

.type-card {
  border: 1px solid #f0f0f0;
  border-radius: 8px;
  padding: 20px 16px;
  text-align: center;
  cursor: pointer;
  transition: border-color 0.2s, box-shadow 0.2s, transform 0.2s;
  background: #fff;
}

.type-card:hover {
  border-color: #7c4dff;
  box-shadow: 0 4px 12px rgba(124, 77, 255, 0.12);
  transform: translateY(-2px);
}

.card-title {
  font-size: 16px;
  font-weight: 600;
  margin: 0 0 8px;
  color: #262626;
}

.card-desc {
  font-size: 12px;
  color: #8c8c8c;
  margin: 0 0 16px;
  min-height: 48px;
}

.card-btn {
  min-width: 80px;
}
</style>
