<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

const route = useRoute()
const router = useRouter()

const copy = computed(() => {
  if (route.path.startsWith('/publish')) {
    return {
      title: '发布不在这里',
      body: '写完文章后，打开「微信」检查配置，再送进公众号草稿箱。不会群发。',
      action: '打开文章',
      to: '/projects',
    }
  }
  if (route.path.startsWith('/analytics')) {
    return {
      title: '还没有可复盘的数据',
      body: '公众号现在只进草稿箱。有阅读数据以后，再看这一页。',
      action: '回写作',
      to: '/',
    }
  }
  return {
    title: '资产库还没做',
    body: '这篇文章用到的图，都在文章页的「配图」里。',
    action: '打开文章',
    to: '/projects',
  }
})
</script>

<template>
  <section class="later">
    <h1>{{ copy.title }}</h1>
    <p>{{ copy.body }}</p>
    <button type="button" @click="router.push(copy.to)">{{ copy.action }}</button>
  </section>
</template>

<style scoped>
.later {
  max-width: 36rem;
  padding-top: 48px;
}

h1 {
  margin: 0 0 10px;
  font-size: clamp(28px, 4vw, 40px);
  font-weight: 560;
  letter-spacing: -0.03em;
  line-height: 1.15;
}

p {
  margin: 0 0 24px;
  color: var(--muted);
  line-height: 1.7;
}

button {
  height: 40px;
  padding: 0 16px;
  border: 0;
  border-radius: 6px;
  background: var(--ink);
  color: var(--surface);
  cursor: pointer;
}

button:active {
  transform: scale(0.98);
}
</style>
