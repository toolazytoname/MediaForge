<script setup lang="ts">
// M10 P2 阶段 F: 全局主题（蚁小二紫 #7C4DFF）+ 中文字体 + zhCN locale
// a-config-provider 已在 ant-design-vue 全局注册，无需导入 ConfigProvider 组件本身
import zhCN from 'ant-design-vue/es/locale/zh_CN'
import AppShell from './layouts/AppShell.vue'

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
    <AppShell />
  </a-config-provider>
</template>