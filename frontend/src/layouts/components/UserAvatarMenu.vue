<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'

interface MenuClickEvent {
  key: string
}

const router = useRouter()
const logoutModalOpen = ref(false)
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
      <span class="user-avatar">{{ username.charAt(0).toUpperCase() }}</span>
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

  <a-modal v-model:open="logoutModalOpen" title="退出登录" :footer="null" width="400px">
    <p class="logout-note">退出还没接上。这台机器上的稿和密钥都还在本地。</p>
  </a-modal>
</template>

<style scoped>
.avatar-trigger {
  display: inline-flex;
  border-radius: 6px;
}

.user-avatar {
  display: grid;
  width: 28px;
  height: 28px;
  place-items: center;
  border-radius: 6px;
  background: var(--ink);
  color: var(--surface);
  font-size: 12px;
  font-weight: 600;
}

.logout-note {
  margin: 0;
  color: var(--muted);
  line-height: 1.65;
}
</style>
