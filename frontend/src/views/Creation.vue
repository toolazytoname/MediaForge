<script setup lang="ts">
// M10-14 P2 阶段 G：图文创作 6 步向导
// a-steps 横向步骤条 + 右侧固定操作面板
// 6 步：选选题 → 创建内容 → 衍生小红书 → AI 出图 → 排期 → 预演
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { storeToRefs } from 'pinia'
import {
  useAccountsStore,
  useCreationStore,
  usePreviewStore,
  type ContentItem,
  type DerivativeResult,
  type ImageGenResult,
  type PublicationItem,
} from '../stores'
import Step1SelectTopic from './Creation/components/Step1SelectTopic.vue'
import Step2Create from './Creation/components/Step2Create.vue'
import Step3Derivative from './Creation/components/Step3Derivative.vue'
import Step4ImageGen from './Creation/components/Step4ImageGen.vue'
import Step5Schedule from './Creation/components/Step5Schedule.vue'
import Step6Preview from './Creation/components/Step6Preview.vue'

// ── Stores（Pinia 单例；子组件各自 useXxxStore 复用同一实例）──
const creationStore = useCreationStore()
const previewStore = usePreviewStore()
const accountsStore = useAccountsStore()

// ── Wizard 状态定义 ──────────────────────────────────────
interface WizardContent {
  id: string
  status: string
  title?: string | null
  pillar?: string | null
}
interface WizardState {
  selectedTopicId: string | null
  content: WizardContent | null
  derivative: DerivativeResult | null
  imageGen: ImageGenResult | null
  publication: PublicationItem | null
}

// 已完成的衍生平台列表（基于 wizard 状态推导，不重新查 DB）
const derivedPlatforms = computed<string[]>(() => {
  const out: string[] = []
  if (wizard.derivative) out.push('xiaohongshu')
  return out
})

// ── Step 元数据 ──────────────────────────────────────────
const stepDefs: { title: string; subtitle: string }[] = [
  { title: '选选题', subtitle: '从 selected 选题里选一条' },
  { title: '创建内容', subtitle: 'LLM 生成 canonical 长文' },
  { title: '衍生小红书', subtitle: '生成 slides + caption + tags' },
  { title: 'AI 出图', subtitle: '出 cover + inline 图' },
  { title: '排期', subtitle: '选平台 + 账号 + 时间' },
  { title: '预演', subtitle: 'dry-run 发布预览' },
]

// ── Wizard 状态 ──────────────────────────────────────────
const currentStep = ref(0) // 0..5
const wizard = reactive<WizardState>({
  selectedTopicId: null,
  content: null,
  derivative: null,
  imageGen: null,
  publication: null,
})

// ── 响应式：窗口宽度 < 768 时 a-steps 改为 vertical ──────
const stepDirection = ref<'vertical' | 'horizontal'>(
  typeof window !== 'undefined' && window.innerWidth < 768 ? 'vertical' : 'horizontal',
)
function updateDirection() {
  stepDirection.value = window.innerWidth < 768 ? 'vertical' : 'horizontal'
}
onMounted(() => {
  updateDirection()
  window.addEventListener('resize', updateDirection)
  // 预先拉账号列表（Step 5 用，不阻塞首屏）
  accountsStore.load().catch(() => null)
})
onUnmounted(() => {
  window.removeEventListener('resize', updateDirection)
})

// ── Step 完成标志 ───────────────────────────────────────
const { running: creationRunning } = storeToRefs(creationStore)
const { lastResult: previewLastResult } = storeToRefs(previewStore)

const stepDone = computed<boolean[]>(() => [
  wizard.selectedTopicId !== null,
  wizard.content !== null,
  wizard.derivative !== null,
  wizard.imageGen !== null,
  wizard.publication !== null,
  previewLastResult.value !== null,
])

const currentStepDone = computed<boolean>(
  () => stepDone.value[currentStep.value] ?? false,
)

// ── 导航 ────────────────────────────────────────────────
function goPrev() {
  if (currentStep.value > 0) currentStep.value--
}
function goNext() {
  if (!currentStepDone.value) return
  if (currentStep.value < stepDefs.length - 1) currentStep.value++
}

// Step 1 → 2：用户点「开始创作」
function beginFromTopic() {
  if (!wizard.selectedTopicId) return
  currentStep.value = 1
}

// Step 2 成功 → 自动跳到 Step 3
function onContentCreated(content: ContentItem) {
  wizard.content = {
    id: content.id,
    status: content.status,
    title: content.title,
    pillar: content.pillar,
  }
  currentStep.value = 2
}

function onDerivativeDone(result: DerivativeResult) {
  wizard.derivative = result
}

function onImageGenDone(result: ImageGenResult) {
  wizard.imageGen = result
}

function onScheduled(pub: PublicationItem) {
  wizard.publication = pub
}

function resetWizard() {
  wizard.selectedTopicId = null
  wizard.content = null
  wizard.derivative = null
  wizard.imageGen = null
  wizard.publication = null
  creationStore.reset()
  previewStore.reset()
  currentStep.value = 0
}

function onTopicChange(id: string | null) {
  wizard.selectedTopicId = id
}
</script>

<template>
  <div class="creation-wizard">
    <!-- 顶部 a-steps + 重置按钮 -->
    <a-card :bordered="false" style="margin-bottom: 16px">
      <div class="wizard-header">
        <h2 style="margin: 0">图文创作向导</h2>
        <a-button size="small" @click="resetWizard">重置向导</a-button>
      </div>
      <a-steps
        :direction="stepDirection"
        :current="currentStep + 1"
        size="small"
        class="wizard-steps"
      >
        <a-step
          v-for="(s, idx) in stepDefs"
          :key="idx"
          :title="s.title"
          :description="s.subtitle"
          :status="
            idx < currentStep ? 'finish'
            : idx === currentStep ? 'process'
            : 'wait'
          "
        >
          <template #icon>
            <span v-if="idx < currentStep">✓</span>
            <span v-else>{{ idx + 1 }}</span>
          </template>
        </a-step>
      </a-steps>
    </a-card>

    <!-- 主体：左上下文 + 右主面板 -->
    <a-row :gutter="16">
      <a-col :xs="24" :md="8" class="context-col">
        <a-card title="当前 content" :bordered="false">
          <a-empty
            v-if="!wizard.content"
            description="尚未创建内容 — 完成 Step 1 + Step 2 后这里会显示 context"
          />
          <template v-else>
            <a-descriptions :column="1" size="small" bordered>
              <a-descriptions-item label="id">
                <code>{{ wizard.content.id }}</code>
              </a-descriptions-item>
              <a-descriptions-item label="status">
                <a-tag color="purple">{{ wizard.content.status }}</a-tag>
              </a-descriptions-item>
              <a-descriptions-item v-if="wizard.content.title" label="title">
                {{ wizard.content.title }}
              </a-descriptions-item>
              <a-descriptions-item v-if="wizard.content.pillar" label="pillar">
                {{ wizard.content.pillar }}
              </a-descriptions-item>
              <a-descriptions-item
                v-if="derivedPlatforms.length"
                label="已衍生 platforms"
              >
                <a-tag
                  v-for="f in derivedPlatforms"
                  :key="f"
                  color="blue"
                  style="margin-right: 4px"
                >
                  {{ f }}
                </a-tag>
              </a-descriptions-item>
              <a-descriptions-item
                v-if="wizard.imageGen && wizard.imageGen.cover_path"
                label="cover_path"
              >
                <code style="font-size: 11px">{{ wizard.imageGen.cover_path }}</code>
              </a-descriptions-item>
              <a-descriptions-item
                v-if="wizard.publication"
                label="已排期 publication"
              >
                <code style="font-size: 11px">{{ wizard.publication.id }}</code>
                <a-tag style="margin-left: 4px">{{ wizard.publication.status }}</a-tag>
              </a-descriptions-item>
            </a-descriptions>
            <a-button
              size="small"
              type="link"
              style="margin-top: 8px"
              @click="$router.push(`/contents/${wizard.content?.id}`)"
            >
              打开内容详情 →
            </a-button>
          </template>
        </a-card>
      </a-col>

      <a-col :xs="24" :md="16">
        <a-card
          :bordered="false"
          :title="`Step ${currentStep + 1} · ${stepDefs[currentStep]?.title ?? ''}`"
        >
          <Step1SelectTopic
            v-show="currentStep === 0"
            :selected-topic-id="wizard.selectedTopicId"
            @update:selected-topic-id="onTopicChange"
            @begin="beginFromTopic"
          />
          <Step2Create
            v-show="currentStep === 1"
            :topic-id="wizard.selectedTopicId"
            @created="onContentCreated"
          />
          <Step3Derivative
            v-show="currentStep === 2"
            :content-id="wizard.content?.id ?? null"
            @done="onDerivativeDone"
          />
          <Step4ImageGen
            v-show="currentStep === 3"
            :content-id="wizard.content?.id ?? null"
            @done="onImageGenDone"
          />
          <Step5Schedule
            v-show="currentStep === 4"
            :content-id="wizard.content?.id ?? null"
            @scheduled="onScheduled"
          />
          <Step6Preview
            v-show="currentStep === 5"
            :publication-id="wizard.publication?.id ?? null"
          />
        </a-card>

        <!-- 底部导航：上一步 / 状态 / 下一步 -->
        <a-card :bordered="false" style="margin-top: 16px">
          <a-space>
            <a-button :disabled="currentStep === 0" @click="goPrev">
              ← 上一步
            </a-button>
            <a-tag v-if="currentStepDone" color="success">✓ 已完成</a-tag>
            <a-tag v-else-if="creationRunning" color="processing">⏳ 进行中</a-tag>
            <a-tag v-else color="default">⏸ 待操作</a-tag>
            <a-button
              v-if="currentStep === 0"
              type="primary"
              :disabled="!wizard.selectedTopicId"
              @click="beginFromTopic"
            >
              ▶ 开始创作
            </a-button>
            <a-button
              v-else
              type="primary"
              :disabled="!currentStepDone"
              @click="goNext"
            >
              下一步 →
            </a-button>
          </a-space>
        </a-card>
      </a-col>
    </a-row>
  </div>
</template>

<style scoped>
.creation-wizard {
  max-width: 100%;
  overflow-x: auto;
}
.wizard-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}
.wizard-steps {
  max-width: 100%;
}
.context-col {
  margin-bottom: 16px;
}
</style>
