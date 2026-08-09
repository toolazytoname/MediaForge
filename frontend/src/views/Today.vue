<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import { ArrowRightOutlined, FolderOpenOutlined, PlusOutlined } from '@ant-design/icons-vue'
import { useProjectsStore } from '../stores'
import { formatDateTime } from '../utils/format'

const router = useRouter()
const projectsStore = useProjectsStore()
const { items, loading, error } = storeToRefs(projectsStore)
const latestProject = computed(() => items.value[0] ?? null)

onMounted(() => projectsStore.load())

function openProject(id: string): void {
  router.push(`/projects/${id}`)
}
</script>

<template>
  <section class="today-page">
    <header class="page-intro">
      <p class="eyebrow">MediaForge</p>
      <h1>今天，继续把一个想法做成作品。</h1>
      <p class="intro-copy">项目是主题、资料、主稿和平台版本共同存在的工作空间。</p>
    </header>

    <a-alert v-if="error" type="error" :message="error" show-icon class="notice" />

    <a-card v-if="latestProject" :bordered="false" class="continue-card">
      <div class="continue-copy">
        <p class="eyebrow">继续项目</p>
        <h2>{{ latestProject.title }}</h2>
        <p>{{ latestProject.goal }}</p>
        <span>上次更新于 {{ formatDateTime(latestProject.updated_at) }}</span>
      </div>
      <a-button type="primary" @click="openProject(latestProject.id)">
        打开工作台 <ArrowRightOutlined />
      </a-button>
    </a-card>

    <a-card v-else-if="!loading" :bordered="false" class="empty-card">
      <FolderOpenOutlined class="empty-icon" />
      <div>
        <h2>从一个值得表达的主题开始</h2>
        <p>先保存想法，或直接设定受众、目标和自主程度来创建一个项目。</p>
        <a-button type="primary" @click="router.push('/projects/new')"><PlusOutlined /> 新建创作项目</a-button>
      </div>
    </a-card>

    <a-card :bordered="false" class="next-card">
      <p class="eyebrow">下一步</p>
      <h2>把真实主题的资料和判断放进同一个项目</h2>
      <p>不要先选择平台，也不必运行流水线。先明确你想帮助谁，以及你愿意为哪一个观点署名。</p>
      <a-button type="link" @click="router.push('/projects')">查看全部项目 <ArrowRightOutlined /></a-button>
    </a-card>
  </section>
</template>

<style scoped>
.today-page { max-width: 980px; padding: 24px 0 56px; }
.page-intro { max-width: 720px; margin-bottom: 28px; }
.eyebrow { margin: 0 0 8px; color: #7a6650; font-size: 12px; font-weight: 700; letter-spacing: .09em; text-transform: uppercase; }
h1, h2 { color: #292522; font-family: Georgia, 'Songti SC', serif; }
h1 { margin: 0 0 12px; font-size: clamp(32px, 4vw, 48px); line-height: 1.16; }
h2 { margin: 0 0 10px; font-size: 24px; }
.intro-copy, .continue-copy p, .empty-card p, .next-card p { color: #706b65; font-size: 16px; line-height: 1.7; }
.notice { margin-bottom: 16px; }
.continue-card, .empty-card, .next-card { margin-bottom: 16px; background: #fffdf8; border: 1px solid #e8e1d5; box-shadow: none; }
.continue-card :deep(.ant-card-body), .empty-card :deep(.ant-card-body) { display: flex; align-items: center; justify-content: space-between; gap: 24px; padding: 28px; }
.continue-copy span { color: #948d84; font-size: 13px; }
.empty-card { background: #fff; }
.empty-card :deep(.ant-card-body) { justify-content: flex-start; }
.empty-icon { color: #b39b79; font-size: 42px; }
.next-card { max-width: 720px; }
@media (max-width: 640px) { .continue-card :deep(.ant-card-body), .empty-card :deep(.ant-card-body) { align-items: flex-start; flex-direction: column; } }
</style>
