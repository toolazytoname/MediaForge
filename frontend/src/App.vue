<script setup lang="ts">
// M10 P2 阶段 F: 全局主题（蚁小二紫 #7C4DFF）+ 中文字体 + zhCN locale
// a-config-provider 已在 ant-design-vue 全局注册，无需导入 ConfigProvider 组件本身
import zhCN from 'ant-design-vue/es/locale/zh_CN'
import { onMounted, onUnmounted, ref } from 'vue'
import AppShell from './layouts/AppShell.vue'

const chunkUpdateRequired = ref(false)
const showChunkUpdateRequired = () => { chunkUpdateRequired.value = true }
onMounted(() => window.addEventListener('mediaforge:chunk-update-required', showChunkUpdateRequired))
onUnmounted(() => window.removeEventListener('mediaforge:chunk-update-required', showChunkUpdateRequired))
const refreshPage = () => window.location.reload()

// 蚁小二风格：紫色主色 + 中文字体栈 + 菜单 active pill 颜色
const theme = {
  token: {
    colorPrimary: '#7C4DFF',
    colorInfo: '#7C4DFF',
    borderRadius: 6,
    fontFamily:
      '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif',
  },
  components: {
    Menu: {
      itemSelectedBg: '#F3EEFF',
      itemSelectedColor: '#7C4DFF',
      itemHoverBg: '#F8F8FA',
    },
  },
}
</script>

<template>
  <a-config-provider :theme="theme" :locale="zhCN">
    <a-alert
      v-if="chunkUpdateRequired"
      class="chunk-update-alert"
      type="warning"
      show-icon
      message="页面已更新"
      description="刚才的页面资源已失效。刷新后可以继续，未保存的输入会保留在当前表单中。"
    >
      <template #action><a-button size="small" @click="refreshPage">刷新页面</a-button></template>
    </a-alert>
    <AppShell />
  </a-config-provider>
</template>

<style scoped>
.chunk-update-alert { position: fixed; top: 16px; right: 16px; z-index: 1001; max-width: 420px; }
</style>
