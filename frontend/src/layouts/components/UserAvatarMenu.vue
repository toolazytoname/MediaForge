<script setup lang="ts">
// 阶段 H（amend）：左上角 32×32 圆头像 + a-dropdown 菜单
// 菜单：用户名（disabled） / 设置 → /settings / 退出 → alert "功能即将上线"
// 用 a-avatar + a-dropdown（trigger=click）+ a-modal 弹提示
import { ref } from 'vue'
import { useRouter } from 'vue-router'

interface MenuClickEvent {
  key: string
}

const router = useRouter()
const logoutModalOpen = ref<boolean>(false)
const username = 'lazy'

function onMenuClick(event: MenuClickEvent): void {
  if (event.key === 'settings') {
    void router.push('/settings')
  } else if (event.key === 'logout') {
    logoutModalOpen.value = true
  }
}
</script>

<template>
  <a-dropdown :trigger="['click']">
    <a class="avatar-trigger" @click.prevent>
      <a-avatar :size="32" class="user-avatar">{{ username.charAt(0).toUpperCase() }}</a-avatar>
    </a>
    <template #overlay>
      <a-menu @click="onMenuClick">
        <a-menu-item key="username" disabled>{{ username }}</a-menu-item>
        <a-menu-divider />
        <a-menu-item key="settings">设置</a-menu-item>
        <a-menu-item key="logout">退出</a-menu-item>
      </a-menu>
    </template>
  </a-dropdown>

  <a-modal
    v-model:open="logoutModalOpen"
    title="退出登录"
    :footer="null"
    width="400px"
  >
    <a-alert
      message="功能即将上线"
      description="退出登录功能将在后续版本提供。"
      type="warning"
      show-icon
    />
  </a-modal>
</template>

<style scoped>
.avatar-trigger {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 4px;
  border-radius: 50%;
  cursor: pointer;
  transition: background-color 0.2s;
}

.avatar-trigger:hover {
  background-color: rgba(124, 77, 255, 0.08);
}

.user-avatar {
  background: #7c4dff;
  color: #fff;
  font-weight: 600;
  cursor: pointer;
}
</style>