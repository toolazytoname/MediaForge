<script setup lang="ts">
// M11-G 双模式创作编辑器：手动草稿 + 编辑现有 draft 共用同一组件
// - 两种模式:create (POST /contents body_markdown) / edit (PATCH /contents/{id})
// - 简易 Markdown 编辑器:大 textarea(本期不接 monaco,保持最小实现)
// - 右侧属性面板:pillar / formats 多选 / 「保存草稿」/「送门禁」按钮
// - 编辑模式下,status 非 draft 时整个表单只读(防直接改 gated+ 内容)
//   仅渲染「该内容已离开 draft,请前往 /contents/{id}」引导
// 视觉参考易撰:左右分栏 + 右侧属性(本期简化版;易撰的热点/AI 改写不抄)
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import axios from 'axios'
import { formatDateTime } from '../utils/format'

const route = useRoute()
const router = useRouter()

interface ContentDetail {
  id: string
  title: string
  pillar: string
  canonical_path: string
  formats: string[]
  status: string
  topic_id: string
}

type Mode = 'create' | 'edit' | 'loading' | 'readonly'
const mode = ref<Mode>('loading')

const contentId = computed<string | null>(() => {
  const id = route.params.id
  return typeof id === 'string' ? id : null
})

const title = ref<string>('')
const pillar = ref<string>('ai_daily')
const bodyMarkdown = ref<string>('')
const formats = ref<string[]>([])
const contentStatus = ref<string>('draft')

const PILLAR_OPTIONS = ['ai_daily', 'finance', 'tech', 'lifestyle', 'uncategorized']
const FORMAT_OPTIONS = [
  { value: 'xhs', label: '小红书' },
  { value: 'x', label: 'X (Twitter)' },
  { value: 'toutiao', label: '头条' },
  { value: 'douyin', label: '抖音' },
  { value: 'article', label: '公众号长文' },
]

const saving = ref<boolean>(false)
const sendGatePending = ref<boolean>(false)
const errorMsg = ref<string | null>(null)
const successMsg = ref<string | null>(null)

const api = axios.create({ baseURL: '/api/v1' })

async function loadContent(id: string) {
  mode.value = 'loading'
  errorMsg.value = null
  try {
    const r = await api.get(`/contents/${id}`)
    const d: ContentDetail = r.data
    title.value = d.title
    pillar.value = d.pillar
    formats.value = Array.isArray(d.formats) ? d.formats : []
    contentStatus.value = d.status
    // 读 canonical.md 内容（如文件读得到；本期用 fetch 文件路径）
    try {
      // canonical_path 是相对路径，直接走 vite 的 fs 不行；
      // 简化：从 /contents/{id} 详情一般含有 canonical_html,这里直接走 markdown
      // 因为 /api/v1/contents/{id} 当前不返回 canonical.md 原文（M10-4 仅给 HTML），
      // 所以本期编辑模式下空 body,提示用户「已加载基本信息,请重新粘贴原文」。
      bodyMarkdown.value = ''
    } catch (e) {
      bodyMarkdown.value = ''
    }
    if (d.status !== 'draft') {
      mode.value = 'readonly'
    } else {
      mode.value = 'edit'
    }
  } catch (e) {
    errorMsg.value = e instanceof Error ? e.message : '加载内容失败'
    mode.value = 'readonly'
  }
}

onMounted(() => {
  if (contentId.value) {
    loadContent(contentId.value)
  } else {
    mode.value = 'create'
  }
})

watch(contentId, (v) => {
  if (v) loadContent(v)
  else mode.value = 'create'
})

const canSave = computed(() => Boolean(
  title.value.trim() && bodyMarkdown.value.trim() && pillar.value,
))

const editable = computed(() => mode.value === 'create' || mode.value === 'edit')

async function saveDraft() {
  if (!canSave.value) return
  saving.value = true
  errorMsg.value = null
  successMsg.value = null
  try {
    if (mode.value === 'create') {
      const r = await api.post('/contents', {
        title: title.value,
        pillar: pillar.value,
        body_markdown: bodyMarkdown.value,
        formats: formats.value,
      })
      successMsg.value = `已创建: ${r.data.id}`
      saving.value = false
      // 跳到编辑模式(同 id)
      await router.push(`/contents/${r.data.id}/edit`)
      return
    }
    if (mode.value === 'edit' && contentId.value) {
      const r = await api.patch(`/contents/${contentId.value}`, {
        title: title.value,
        pillar: pillar.value,
        body_markdown: bodyMarkdown.value,
        formats: formats.value,
      })
      successMsg.value = `已保存 ${r.data.id} @ ${formatDateTime(r.data.updated_at)}`
    }
  } catch (e: unknown) {
    if (axios.isAxiosError(e) && e.response) {
      const code = e.response.data?.detail?.error?.code ?? 'unknown'
      const msg = e.response.data?.detail?.error?.message ?? '请求失败'
      errorMsg.value = `${code}: ${msg}`
    } else {
      errorMsg.value = e instanceof Error ? e.message : String(e)
    }
  } finally {
    saving.value = false
  }
}

async function sendToGate() {
  // 「送门禁」= 复用 M10-10 /contents/{id}/gate 或类似端点(若存在)
  // 当前实现：先确保 draft 已保存,再跳转到 /contents/{id} 让用户从那里触发门禁
  if (mode.value === 'create') {
    errorMsg.value = '请先保存草稿再送门禁'
    return
  }
  if (!contentId.value) return
  await router.push(`/contents/${contentId.value}`)
}
</script>

<template>
  <div>
    <h2 v-if="mode === 'create'">新建草稿（手动创作）</h2>
    <h2 v-else-if="mode === 'edit'">编辑草稿 {{ contentId }}</h2>
    <h2 v-else-if="mode === 'readonly'">内容 {{ contentId }}（非 draft,不可编辑）</h2>
    <h2 v-else>加载中...</h2>

    <a-alert
      v-if="errorMsg"
      type="error"
      show-icon
      :message="errorMsg"
      style="margin-bottom: 12px"
      @close="errorMsg = null"
    />
    <a-alert
      v-if="successMsg"
      type="success"
      show-icon
      :message="successMsg"
      style="margin-bottom: 12px"
      @close="successMsg = null"
    />

    <a-row v-if="mode !== 'readonly' && mode !== 'loading'" :gutter="16">
      <!-- 左:编辑器 -->
      <a-col :span="16">
        <a-card title="编辑器" size="small">
          <a-form layout="vertical">
            <a-form-item label="标题">
              <a-input
                v-model:value="title"
                placeholder="文章标题"
                :disabled="!editable"
                size="large"
              />
            </a-form-item>
            <a-form-item label="正文 (Markdown)">
              <a-textarea
                v-model:value="bodyMarkdown"
                placeholder="# 标题&#10;&#10;正文..."
                :auto-size="{ minRows: 16, maxRows: 32 }"
                :disabled="!editable"
                style="font-family: ui-monospace, SFMono-Regular, Menlo, monospace;"
              />
            </a-form-item>
          </a-form>
        </a-card>
      </a-col>

      <!-- 右:属性面板 -->
      <a-col :span="8">
        <a-card title="属性" size="small" style="margin-bottom: 12px">
          <a-form layout="vertical">
            <a-form-item label="主题 (pillar)">
              <a-select
                v-model:value="pillar"
                :options="PILLAR_OPTIONS.map((p) => ({ value: p, label: p }))"
                :disabled="!editable"
              />
            </a-form-item>
            <a-form-item label="目标平台 (formats)">
              <a-select
                v-model:value="formats"
                :options="FORMAT_OPTIONS"
                mode="multiple"
                placeholder="选平台"
                :disabled="!editable"
                allow-clear
              />
            </a-form-item>
          </a-form>
          <a-space direction="vertical" style="width: 100%">
            <a-button
              type="primary"
              block
              :disabled="!canSave || saving"
              :loading="saving"
              @click="saveDraft"
            >
              {{ mode === 'create' ? '💾 创建草稿' : '💾 保存草稿' }}
            </a-button>
            <a-button
              v-if="mode === 'edit'"
              block
              :disabled="sendGatePending"
              @click="sendToGate"
            >
              🚦 前往详情送门禁
            </a-button>
          </a-space>
        </a-card>

        <a-card title="说明" size="small">
          <p style="font-size: 12px; color: #8c8c8c; margin: 0">
            手动创作不调用 LLM，直接产出 draft。
            保存后会和 AI 自动生成的内容共用 gate → review → publish 后半程。
          </p>
          <p v-if="mode === 'create'" style="font-size: 12px; color: #8c8c8c; margin-top: 8px">
            保存后会自动跳转到编辑页，方便继续打磨。
          </p>
          <p v-else style="font-size: 12px; color: #8c8c8c; margin-top: 8px">
            重新粘贴正文即可整体覆盖。本期编辑模式不会回填 canonical.md 原文（M10-4 接口仅返回 HTML）。
          </p>
        </a-card>
      </a-col>
    </a-row>

    <a-empty
      v-if="mode === 'readonly'"
      description="该内容已不在 draft 阶段，请前往内容详情页查看。"
      style="margin-top: 32px"
    >
      <a-button v-if="contentId" type="primary" @click="router.push(`/contents/${contentId}`)">
        前往内容详情
      </a-button>
    </a-empty>

    <a-empty
      v-if="mode === 'loading'"
      description="加载中..."
      style="margin-top: 32px"
    />
  </div>
</template>
