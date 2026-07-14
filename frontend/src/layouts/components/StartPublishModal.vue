<script setup lang="ts">
// 阶段 H（amend）：4 选 1 弹窗（照抄蚁小二）
// 标题：「选择发布类型」；4 张卡 2×2 grid
// - 视频发布：支持平台 (35) → 跳 /creation/video（M12-3 视频创作向导）
// - 图文发布：支持平台 (9) → 跳 /creation
// - 文章发布：支持平台 (19) → 占位 alert "即将上线"
// - 公众号：占位 alert "即将上线"
import { ref } from 'vue'
import { useRouter } from 'vue-router'

interface Props {
  open: boolean
}

interface PublishOption {
  key: 'video' | 'image' | 'article' | 'wechat'
  title: string
  desc: string
  platforms: number
}

defineProps<Props>()
const emit = defineEmits<{
  (e: 'update:open', value: boolean): void
}>()

const router = useRouter()
const comingSoonOpen = ref<boolean>(false)

const options: ReadonlyArray<PublishOption> = [
  { key: 'video', title: '视频发布', desc: '支持平台', platforms: 35 },
  { key: 'image', title: '图文发布', desc: '支持平台', platforms: 9 },
  { key: 'article', title: '文章发布', desc: '支持平台', platforms: 19 },
  { key: 'wechat', title: '公众号', desc: '', platforms: 0 },
]

function close(value: boolean): void {
  emit('update:open', value)
}

function onSelect(option: PublishOption): void {
  if (option.key === 'image') {
    // 图文：跳 /creation 6 步向导
    close(false)
    void router.push('/creation')
    return
  }
  if (option.key === 'video') {
    // 视频：跳 /creation/video 6 步向导（M12-3）
    close(false)
    void router.push('/creation/video')
    return
  }
  // 其他两种：占位提示
  close(false)
  comingSoonOpen.value = true
}
</script>

<template>
  <a-modal
    :open="open"
    title="选择发布类型"
    :footer="null"
    width="640px"
    @update:open="close"
  >
    <div class="publish-grid">
      <div
        v-for="opt in options"
        :key="opt.key"
        class="publish-card"
        @click="onSelect(opt)"
      >
        <h3 class="card-title">{{ opt.title }}</h3>
        <p v-if="opt.platforms > 0" class="card-desc">{{ opt.desc }} ({{ opt.platforms }})</p>
        <p v-else class="card-desc">&nbsp;</p>
        <a-button type="primary" class="card-btn">开始发布</a-button>
      </div>
    </div>
  </a-modal>

  <a-modal
    v-model:open="comingSoonOpen"
    title="提示"
    :footer="null"
    width="400px"
  >
    <a-alert
      message="功能即将上线"
      description="该发布类型将在后续版本提供。"
      type="info"
      show-icon
    />
  </a-modal>
</template>

<style scoped>
.publish-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  padding: 8px 0;
}

.publish-card {
  border: 1px solid #f0f0f0;
  border-radius: 8px;
  padding: 24px 20px;
  text-align: center;
  cursor: pointer;
  transition: border-color 0.2s, box-shadow 0.2s, transform 0.2s;
  background: #fff;
}

.publish-card:hover {
  border-color: #7c4dff;
  box-shadow: 0 4px 12px rgba(124, 77, 255, 0.12);
  transform: translateY(-2px);
}

.card-title {
  font-size: 18px;
  font-weight: 600;
  margin: 0 0 8px;
  color: #262626;
}

.card-desc {
  font-size: 13px;
  color: #8c8c8c;
  margin: 0 0 20px;
  min-height: 20px;
}

.card-btn {
  min-width: 100px;
}
</style>