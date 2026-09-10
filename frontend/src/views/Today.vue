<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowRightOutlined, PlusOutlined, WarningOutlined } from '@ant-design/icons-vue'
import { api, unwrapError } from '../api/client'

interface TodayItem {
  kind: string
  title: string
  detail: string
  href: string
  project_id: string | null
}

const router = useRouter()
const todos = ref<TodayItem[]>([])
const exceptions = ref<TodayItem[]>([])
const nextPlans = ref<TodayItem[]>([])
const loading = ref(false)
const error = ref('')

onMounted(async () => {
  loading.value = true
  try {
    const response = await api.get<{ todos: TodayItem[]; exceptions: TodayItem[]; next_plans: TodayItem[] }>('/today')
    todos.value = response.data.todos
    exceptions.value = response.data.exceptions
    nextPlans.value = response.data.next_plans
  } catch (err) {
    error.value = unwrapError(err)
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <section class="today-page">
    <header class="page-intro">
      <p class="eyebrow">今天</p>
      <h1>先处理真实待办，而不是后台状态。</h1>
    </header>
    <a-alert v-if="error" type="error" :message="error" show-icon class="notice" />
    <a-card v-if="exceptions.length" :bordered="false" class="block">
      <p class="eyebrow">异常</p>
      <button v-for="item in exceptions" :key="item.title + item.href" class="row" type="button" @click="router.push(item.href)">
        <WarningOutlined />
        <div><h2>{{ item.title }}</h2><p>{{ item.detail }}</p></div>
        <ArrowRightOutlined />
      </button>
    </a-card>
    <a-card :bordered="false" class="block">
      <p class="eyebrow">待办</p>
      <a-spin :spinning="loading">
        <button v-for="item in todos" :key="item.href" class="row" type="button" @click="router.push(item.href)">
          <div><h2>{{ item.title }}</h2><p>{{ item.detail }}</p></div>
          <ArrowRightOutlined />
        </button>
        <div v-if="!loading && !todos.length" class="empty">
          <p>还没有进行中的作品。</p>
          <a-button type="primary" @click="router.push('/projects/new')"><PlusOutlined /> 开始创作</a-button>
        </div>
      </a-spin>
    </a-card>
    <a-card :bordered="false" class="block">
      <p class="eyebrow">下一次计划</p>
      <p v-if="!nextPlans.length" class="muted">账号运营未开启时，这里保持空白。</p>
      <button v-for="item in nextPlans" :key="item.href + item.title" class="row" type="button" @click="router.push(item.href)">
        <div><h2>{{ item.title }}</h2><p>{{ item.detail }}</p></div>
      </button>
    </a-card>
  </section>
</template>

<style scoped>
.today-page { max-width: 820px; padding: 24px 0 56px; }
.eyebrow { margin: 0 0 8px; color: #7a6650; font-size: 12px; font-weight: 700; letter-spacing: .08em; }
h1 { font-family: Georgia, 'Songti SC', serif; font-size: clamp(28px, 4vw, 40px); }
.notice { margin-bottom: 16px; }
.block { margin-bottom: 16px; background: #fffdf8; border: 1px solid #e8e1d5; }
.row { display: flex; width: 100%; align-items: center; justify-content: space-between; gap: 12px; padding: 12px 0; border: 0; background: transparent; text-align: left; cursor: pointer; }
h2 { margin: 0; font-size: 18px; }
.row p, .muted, .empty p { color: #706b65; }
.empty { padding: 12px 0 8px; }
</style>
