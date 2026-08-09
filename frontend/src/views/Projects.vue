<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import { ArrowLeftOutlined, ArrowRightOutlined, FolderOpenOutlined } from '@ant-design/icons-vue'
import { useProjectsStore, type ProjectItem } from '../stores'
import { unwrapError } from '../api/client'
import { formatDateTime } from '../utils/format'

const route = useRoute()
const router = useRouter()
const store = useProjectsStore()
const { items, total, loading, error } = storeToRefs(store)
const project = ref<ProjectItem | null>(null)
const detailError = ref<string | null>(null)
const projectId = computed(() => typeof route.params.id === 'string' ? route.params.id : null)

async function loadPage(): Promise<void> {
  detailError.value = null
  project.value = null
  if (!projectId.value) {
    await store.load()
    return
  }
  try {
    project.value = await store.getDetail(projectId.value)
  } catch (e) {
    detailError.value = unwrapError(e)
  }
}

onMounted(loadPage)
watch(projectId, loadPage)
</script>

<template>
  <section class="projects-page">
    <template v-if="projectId">
      <a-button type="link" class="back" @click="router.push('/projects')"><ArrowLeftOutlined /> 全部项目</a-button>
      <a-alert v-if="detailError" type="error" :message="detailError" show-icon />
      <article v-else-if="project" class="project-workspace">
        <header>
          <p class="eyebrow">主题项目</p>
          <h1>{{ project.title }}</h1>
          <p class="idea">{{ project.idea }}</p>
        </header>
        <div class="project-grid">
          <a-card title="创作意图" :bordered="false"><dl><dt>写给谁</dt><dd>{{ project.audience }}</dd><dt>这次要完成什么</dt><dd>{{ project.goal }}</dd><dt>声音</dt><dd>{{ project.voice }}</dd></dl></a-card>
          <a-card title="目前的材料" :bordered="false"><p>已关联 {{ project.content_ids.length }} 篇内容，{{ project.asset_paths.length }} 项资产。</p><p class="muted">研究、主稿和视觉计划会在后续步骤进入这里。</p></a-card>
        </div>
        <a-card :bordered="false" class="next-action"><p class="eyebrow">下一步</p><h2>整理资料，确定这个主题的独特主张</h2><p>项目已经保存了创作意图。下一阶段会在这里建立研究板和共创编辑器。</p></a-card>
      </article>
    </template>

    <template v-else>
      <header class="list-header"><div><p class="eyebrow">项目</p><h1>每一个主题，都有一张自己的工作台。</h1><p>项目把想法、资料、主稿、视觉与平台版本放在同一条创作路径上。</p></div><a-button type="primary" disabled>新建项目</a-button></header>
      <a-alert v-if="error" type="error" :message="error" show-icon class="notice" />
      <a-spin :spinning="loading">
        <div v-if="items.length" class="project-list"><button v-for="item in items" :key="item.id" class="project-row" @click="router.push(`/projects/${item.id}`)"><div><h2>{{ item.title }}</h2><p>{{ item.idea }}</p><span>{{ item.audience }} · {{ item.goal }}</span></div><div class="row-meta"><time>{{ formatDateTime(item.updated_at) }}</time><ArrowRightOutlined /></div></button></div>
        <a-empty v-else-if="!loading" description="还没有项目。下一步可以从一个真实主题开始。"><template #image><FolderOpenOutlined class="empty-icon" /></template></a-empty>
      </a-spin>
      <p class="count" v-if="total">共 {{ total }} 个项目</p>
    </template>
  </section>
</template>

<style scoped>
.projects-page { max-width: 1020px; padding: 24px 0 56px; }
.eyebrow { margin: 0 0 8px; color: #7a6650; font-size: 12px; font-weight: 700; letter-spacing: .09em; text-transform: uppercase; }
h1, h2 { color: #292522; font-family: Georgia, 'Songti SC', serif; } h1 { margin: 0 0 12px; font-size: clamp(30px, 4vw, 44px); line-height: 1.2; } h2 { margin: 0 0 8px; font-size: 22px; }
.list-header { display: flex; justify-content: space-between; align-items: flex-end; gap: 20px; margin-bottom: 28px; }.list-header > div { max-width: 720px; }.list-header p, .idea, .project-row p, .project-row span, .project-workspace p { color: #706b65; line-height: 1.7; }.notice { margin-bottom: 16px; }
.project-list { border-top: 1px solid #ded7cd; }.project-row { width: 100%; display: flex; justify-content: space-between; gap: 24px; padding: 22px 4px; text-align: left; border: 0; border-bottom: 1px solid #ded7cd; background: transparent; cursor: pointer; }.project-row:hover h2 { color: #886d4b; }.project-row p { max-width: 700px; margin: 0 0 6px; }.project-row span, .row-meta { color: #948d84; font-size: 13px; }.row-meta { display: flex; align-items: center; gap: 16px; white-space: nowrap; }.empty-icon { color: #b39b79; font-size: 44px; }.count { color: #948d84; font-size: 13px; }.back { margin-bottom: 12px; padding-left: 0; }.project-workspace > header { max-width: 760px; margin-bottom: 28px; }.idea { font-size: 18px; }.project-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }.project-grid :deep(.ant-card), .next-action { background: #fffdf8; border: 1px solid #e8e1d5; box-shadow: none; }.project-grid dd { margin: 4px 0 16px; color: #4e4943; }.project-grid dt { color: #948d84; font-size: 12px; }.next-action { margin-top: 16px; max-width: 760px; } .muted { color: #948d84 !important; }
@media (max-width: 640px) { .list-header, .project-row { align-items: flex-start; flex-direction: column; }.project-grid { grid-template-columns: 1fr; }.row-meta { width: 100%; justify-content: space-between; } }
</style>
