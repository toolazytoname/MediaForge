<script setup lang="ts">
import zhCN from 'ant-design-vue/es/locale/zh_CN'
import AppShell from './layouts/AppShell.vue'

const theme = {
  token: {
    colorPrimary: '#1c1c1a',
    colorInfo: '#1c1c1a',
    colorSuccess: '#2a5a3d',
    colorWarning: '#7a5a20',
    colorError: '#8c2f28',
    colorText: '#1c1c1a',
    colorTextSecondary: '#6e6e68',
    colorBorder: '#e4e4df',
    colorBgLayout: '#f2f2f0',
    colorBgContainer: '#ffffff',
    borderRadius: 6,
    fontFamily:
      '"PingFang SC", "Hiragino Sans GB", "Noto Sans SC", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
  },
  components: {
    Menu: {
      itemSelectedBg: '#ecece8',
      itemSelectedColor: '#1c1c1a',
      itemHoverBg: '#f2f2f0',
    },
    Button: {
      primaryShadow: 'none',
      defaultShadow: 'none',
    },
  },
}
</script>

<template>
  <a-config-provider :theme="theme" :locale="zhCN">
    <a href="#main" class="skip-link">跳到正文</a>
    <AppShell />
  </a-config-provider>
</template>
