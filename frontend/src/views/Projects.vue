<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import { ArrowLeftOutlined, ArrowRightOutlined, FolderOpenOutlined } from '@ant-design/icons-vue'
import { useProjectsStore, useResearchStore, type ProjectItem, type ResearchClaim } from '../stores'
import { unwrapError } from '../api/client'
import { formatDateTime } from '../utils/format'

const route = useRoute()
const router = useRouter()
const store = useProjectsStore()
const researchStore = useResearchStore()
const { items, total, loading, error } = storeToRefs(store)
const { board, loading: researchLoading, error: researchError } = storeToRefs(researchStore)
const project = ref<ProjectItem | null>(null)
const detailError = ref<string | null>(null)
const projectId = computed(() => typeof route.params.id === 'string' ? route.params.id : null)
const sourceForm = ref({ title: '', reference: '', summary: '' })
const claimForm = ref<{ text: string; kind: ResearchClaim['kind']; status: ResearchClaim['status']; source_ids: string[]; limitation: string; counterpoint: string }>({
  text: '', kind: 'fact', status: 'unverified', source_ids: [], limitation: '', counterpoint: '',
})
const sourceSaving = ref(false)
const claimSaving = ref(false)

const unverifiedFacts = computed(() => board.value?.claims.filter(item => item.kind === 'fact' && (item.status === 'unverified' || !item.source_ids.length)) ?? [])
const openQuestions = computed(() => board.value?.claims.filter(item => item.kind === 'open_question' && item.status === 'open') ?? [])
const claimStatusOptions = computed(() => claimForm.value.kind === 'open_question'
  ? [{ value: 'open', label: '待解决' }, { value: 'resolved', label: '已解决' }]
  : [{ value: 'unverified', label: '待核查' }, { value: 'verified', label: '已核查' }])

async function loadPage(): Promise<void> {
  detailError.value = null
  project.value = null
  if (!projectId.value) {
    await store.load()
    return
  }
  try {
    project.value = await store.getDetail(projectId.value)
    await researchStore.load(projectId.value)
  } catch (e) {
    detailError.value = unwrapError(e)
  }
}

function updateStatusForKind(): void {
  claimForm.value.status = claimForm.value.kind === 'open_question' ? 'open' : 'unverified'
}

async function saveSource(): Promise<void> {
  if (!projectId.value) return
  sourceSaving.value = true
  try {
    await researchStore.addSource(projectId.value, sourceForm.value)
    sourceForm.value = { title: '', reference: '', summary: '' }
  } catch (e) { detailError.value = unwrapError(e) } finally { sourceSaving.value = false }
}

async function saveClaim(): Promise<void> {
  if (!projectId.value) return
  claimSaving.value = true
  try {
    await researchStore.addClaim(projectId.value, {
      text: claimForm.value.text, kind: claimForm.value.kind, status: claimForm.value.status,
      source_ids: claimForm.value.source_ids, limitation: claimForm.value.limitation || null,
      counterpoint: claimForm.value.counterpoint || null,
    })
    claimForm.value = { text: '', kind: 'fact', status: 'unverified', source_ids: [], limitation: '', counterpoint: '' }
  } catch (e) { detailError.value = unwrapError(e) } finally { claimSaving.value = false }
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
          <a-card title="目前的材料" :bordered="false"><p>已关联 {{ project.content_ids.length }} 篇内容，{{ project.asset_paths.length }} 项资产。</p><p class="muted">来源、判断和待确认项均由你明确录入，不会自动抓取或改写。</p></a-card>
        </div>
        <section class="research-board">
          <header class="section-heading"><div><p class="eyebrow">研究板</p><h2>先把依据、判断和未知写清楚。</h2><p>来源不会自动核查。标记为“已核查”前，请自行确认原始材料。</p></div></header>
          <a-alert v-if="researchError" type="error" :message="researchError" show-icon class="notice" />
          <a-spin :spinning="researchLoading">
            <div class="research-alerts" v-if="unverifiedFacts.length || openQuestions.length">
              <a-alert v-if="unverifiedFacts.length" type="warning" show-icon :message="`${unverifiedFacts.length} 条事实尚未核查或缺少来源`" />
              <a-alert v-if="openQuestions.length" type="info" show-icon :message="`${openQuestions.length} 个问题仍待解决`" />
            </div>
            <div class="research-grid">
              <a-card title="来源" :bordered="false">
                <div v-if="board?.sources.length" class="record-list source-list"><article v-for="source in board.sources" :key="source.id"><a :href="source.reference" target="_blank" rel="noreferrer">{{ source.title }}</a><p>{{ source.summary }}</p><small>{{ source.reference }}</small></article></div>
                <a-empty v-else description="还没有来源" :image-style="{ height: '40px' }" />
                <a-form layout="vertical" class="research-form" @finish="saveSource"><a-form-item label="标题" required><a-input v-model:value="sourceForm.title" /></a-form-item><a-form-item label="URL 或本地引用" required><a-input v-model:value="sourceForm.reference" placeholder="https://… 或 notes/interview.md" /></a-form-item><a-form-item label="摘要" required><a-textarea v-model:value="sourceForm.summary" :rows="2" /></a-form-item><a-button html-type="button" :loading="sourceSaving" @click="saveSource">添加来源</a-button></a-form>
              </a-card>
              <a-card title="声明" :bordered="false">
                <div v-if="board?.claims.length" class="record-list claim-list"><article v-for="claim in board.claims" :key="claim.id" :class="{ unresolved: claim.kind === 'open_question' && claim.status === 'open', unverified: claim.kind === 'fact' && (claim.status === 'unverified' || !claim.source_ids.length) }"><div class="claim-meta"><a-tag :color="claim.kind === 'fact' ? 'blue' : claim.kind === 'judgment' ? 'gold' : 'purple'">{{ claim.kind === 'fact' ? '事实' : claim.kind === 'judgment' ? '判断' : '待确认' }}</a-tag><a-tag>{{ claim.status === 'verified' ? '已核查' : claim.status === 'resolved' ? '已解决' : claim.status === 'open' ? '待解决' : '待核查' }}</a-tag></div><p>{{ claim.text }}</p><small v-if="claim.source_ids.length">关联 {{ claim.source_ids.length }} 个来源</small><small v-else>未关联来源</small><p v-if="claim.limitation" class="caveat">限制：{{ claim.limitation }}</p><p v-if="claim.counterpoint" class="caveat">反方：{{ claim.counterpoint }}</p></article></div>
                <a-empty v-else description="还没有声明" :image-style="{ height: '40px' }" />
                <a-form layout="vertical" class="research-form" @finish="saveClaim"><a-form-item label="声明" required><a-textarea v-model:value="claimForm.text" :rows="2" /></a-form-item><div class="form-pair"><a-form-item label="类型" required><a-select v-model:value="claimForm.kind" @change="updateStatusForKind"><a-select-option value="fact">事实</a-select-option><a-select-option value="judgment">个人判断</a-select-option><a-select-option value="open_question">待确认问题</a-select-option></a-select></a-form-item><a-form-item label="状态" required><a-select v-model:value="claimForm.status"><a-select-option v-for="option in claimStatusOptions" :key="option.value" :value="option.value">{{ option.label }}</a-select-option></a-select></a-form-item></div><a-form-item label="关联来源"><a-select v-model:value="claimForm.source_ids" mode="multiple" placeholder="可留空，但事实会显示待核查"><a-select-option v-for="source in board?.sources ?? []" :key="source.id" :value="source.id">{{ source.title }}</a-select-option></a-select></a-form-item><a-form-item label="限制"><a-textarea v-model:value="claimForm.limitation" :rows="1" placeholder="可选" /></a-form-item><a-form-item label="反方观点"><a-textarea v-model:value="claimForm.counterpoint" :rows="1" placeholder="可选" /></a-form-item><a-button html-type="button" :loading="claimSaving" @click="saveClaim">添加声明</a-button></a-form>
              </a-card>
            </div>
          </a-spin>
        </section>
      </article>
    </template>

    <template v-else>
      <header class="list-header"><div><p class="eyebrow">项目</p><h1>每一个主题，都有一张自己的工作台。</h1><p>项目把想法、资料、主稿、视觉与平台版本放在同一条创作路径上。</p></div><a-button type="primary" @click="router.push('/projects/new')">新建项目</a-button></header>
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
.project-list { border-top: 1px solid #ded7cd; }.project-row { width: 100%; display: flex; justify-content: space-between; gap: 24px; padding: 22px 4px; text-align: left; border: 0; border-bottom: 1px solid #ded7cd; background: transparent; cursor: pointer; }.project-row:hover h2 { color: #886d4b; }.project-row p { max-width: 700px; margin: 0 0 6px; }.project-row span, .row-meta { color: #948d84; font-size: 13px; }.row-meta { display: flex; align-items: center; gap: 16px; white-space: nowrap; }.empty-icon { color: #b39b79; font-size: 44px; }.count { color: #948d84; font-size: 13px; }.back { margin-bottom: 12px; padding-left: 0; }.project-workspace > header { max-width: 760px; margin-bottom: 28px; }.idea { font-size: 18px; }.project-grid, .research-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }.project-grid :deep(.ant-card), .research-grid :deep(.ant-card) { background: #fffdf8; border: 1px solid #e8e1d5; box-shadow: none; }.project-grid dd { margin: 4px 0 16px; color: #4e4943; }.project-grid dt { color: #948d84; font-size: 12px; }.muted { color: #948d84 !important; }.research-board { margin-top: 34px; max-width: 1000px; }.section-heading { margin-bottom: 18px; }.section-heading p { max-width: 680px; }.research-alerts { display: grid; gap: 8px; margin-bottom: 16px; }.record-list { display: grid; gap: 10px; margin-bottom: 20px; }.record-list article { padding: 12px; border-left: 3px solid #d8c9b5; background: #faf7f1; }.record-list p { margin: 6px 0; color: #5e5851; }.record-list small { color: #897f75; word-break: break-word; }.claim-meta { display: flex; gap: 6px; }.claim-list .unverified { border-left-color: #d89614; }.claim-list .unresolved { border-left-color: #7f59b0; }.caveat { color: #7a5d3d !important; }.research-form { padding-top: 12px; border-top: 1px solid #e8e1d5; }.form-pair { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
@media (max-width: 640px) { .list-header, .project-row { align-items: flex-start; flex-direction: column; }.project-grid, .research-grid, .form-pair { grid-template-columns: 1fr; }.row-meta { width: 100%; justify-content: space-between; } }
</style>
