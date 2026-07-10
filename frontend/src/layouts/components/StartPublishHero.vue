<script setup lang="ts">
// 阶段 H（amend）：主区域中问的"开始发布" hero 大卡
// 紫色渐变背景 + 大图标 + 主标题 + 副标题 + "立即开始"按钮
// 点击按钮触发 emit('click')，由父组件（AppShell）打开 4 选 1 弹窗
defineEmits<{
  (e: 'click'): void
}>()
</script>

<template>
  <div class="hero-card" @click="$emit('click')">
    <div class="hero-icon">
      <span class="icon-play">▶</span>
    </div>
    <h2 class="hero-title">开始发布</h2>
    <p class="hero-desc">一键发布视频、图文、文章至自媒体平台</p>
    <a-button type="primary" size="large" class="hero-btn">
      立即开始
    </a-button>
  </div>
</template>

<style scoped>
.hero-card {
  background: linear-gradient(135deg, #7c4dff 0%, #5e35b1 100%);
  border-radius: 12px;
  padding: 48px 32px;
  color: #fff;
  text-align: center;
  cursor: pointer;
  transition: transform 0.2s, box-shadow 0.2s;
  box-shadow: 0 4px 16px rgba(124, 77, 255, 0.2);
  max-width: 720px;
  margin: 0 auto 24px;
}

.hero-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 8px 24px rgba(124, 77, 255, 0.3);
}

.hero-icon {
  width: 72px;
  height: 72px;
  margin: 0 auto 20px;
  background: rgba(255, 255, 255, 0.18);
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
}

.icon-play {
  font-size: 32px;
  color: #fff;
  margin-left: 4px; /* optical center */
}

.hero-title {
  font-size: 28px;
  font-weight: 600;
  margin: 0 0 12px;
  color: #fff;
}

.hero-desc {
  font-size: 15px;
  margin: 0 0 28px;
  color: rgba(255, 255, 255, 0.85);
}

.hero-btn {
  background: #fff;
  color: #7c4dff;
  border: none;
  font-weight: 600;
  height: 44px;
  padding: 0 32px;
  font-size: 15px;
}

.hero-btn:hover {
  background: #f3eeff !important;
  color: #7c4dff !important;
}
</style>