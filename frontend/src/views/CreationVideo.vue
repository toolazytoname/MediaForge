<script setup lang="ts">
// M12-3 视频创作 6 步向导：选内容 → 选类型 → 生成口播稿 → 引擎参数 → 提交轮询 → 预览
// 严格镜像 Creation.vue 的向导范式（reactive WizardState + stepDefs + stepDone
// computed + v-show 步骤面板），UI 不直连 DB/引擎，只走 video_bridge 分层的
// /contents/{id}/video-script、/video-jobs 接口。
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { useVideoCreationStore, type VideoJobResult } from '../stores'
import Step1SelectContent from './CreationVideo/components/Step1SelectContent.vue'
import Step2SelectType from './CreationVideo/components/Step2SelectType.vue'
import Step3Script from './CreationVideo/components/Step3Script.vue'
import Step4EngineParams from './CreationVideo/components/Step4EngineParams.vue'
import Step5SubmitPoll from './CreationVideo/components/Step5SubmitPoll.vue'
import Step6Preview from './CreationVideo/components/Step6Preview.vue'

const videoStore = useVideoCreationStore()
const { engine, script, style } = storeToRefs(videoStore)

// ── Wizard 状态 ──────────────────────────────────────────
interface WizardState {
  contentId: string | null
  durationS: number
}

const stepDefs: { title: string; subtitle: string }[] = [
  { title: '选内容', subtitle: '选一条已有内容作为视频原文' },
  { title: '选类型', subtitle: '素材混剪 / AI 生成视频 / 数字人口播' },
  { title: '生成口播稿', subtitle: 'LLM 派生脚本，可编辑' },
  { title: '引擎参数', subtitle: '时长/画幅/音色/形象模板' },
  { title: '提交与轮询', subtitle: '提交任务，等待引擎生成' },
  { title: '预览', subtitle: '播放生成的视频' },
]

const currentStep = ref(0) // 0..5
const wizard = reactive<WizardState>({
  contentId: null,
  durationS: 60,
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
})
onUnmounted(() => {
  window.removeEventListener('resize', updateDirection)
  videoStore.stopPolling()
})

// ── Step 完成标志 ───────────────────────────────────────
const { job } = storeToRefs(videoStore)

const engineParamsReady = computed<boolean>(() => {
  if (engine.value === 'digitalhuman') {
    return Boolean(style.value.avatar_template)
  }
  return true
})

const stepDone = computed<boolean[]>(() => [
  wizard.contentId !== null,
  engine.value !== null,
  script.value.trim().length > 0,
  engineParamsReady.value,
  job.value?.state === 'done',
  job.value?.state === 'done',
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

function onContentSelected(id: string | null) {
  wizard.contentId = id
}
function beginFromContent() {
  if (!wizard.contentId) return
  currentStep.value = 1
}

function onEngineSelected(v: 'mpt' | 'pixelle' | 'digitalhuman') {
  engine.value = v
}

function onDurationChange(v: number) {
  wizard.durationS = v
}

function onSubmitDone(_j: VideoJobResult) {
  // Step5 内部已更新 store.job，这里不需要额外处理；保留 hook 以便未来扩展
}

function resetWizard() {
  wizard.contentId = null
  wizard.durationS = 60
  videoStore.reset()
  currentStep.value = 0
}
</script>

<template>
  <div class="creation-video-wizard">
    <a-card :bordered="false" style="margin-bottom: 16px">
      <div class="wizard-header">
        <h2 style="margin: 0">视频创作向导</h2>
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

    <a-row :gutter="16">
      <a-col :xs="24" :md="8" class="context-col">
        <a-card title="当前状态" :bordered="false">
          <a-descriptions :column="1" size="small" bordered>
            <a-descriptions-item label="content_id">
              <code v-if="wizard.contentId">{{ wizard.contentId }}</code>
              <span v-else style="color: #8c8c8c">未选择</span>
            </a-descriptions-item>
            <a-descriptions-item label="engine">
              <a-tag v-if="engine" color="purple">{{ engine }}</a-tag>
              <span v-else style="color: #8c8c8c">未选择</span>
            </a-descriptions-item>
            <a-descriptions-item label="duration_s">
              {{ wizard.durationS }}
            </a-descriptions-item>
            <a-descriptions-item v-if="job" label="job 状态">
              <a-tag>{{ job.state }}</a-tag>
            </a-descriptions-item>
          </a-descriptions>
        </a-card>
      </a-col>

      <a-col :xs="24" :md="16">
        <a-card
          :bordered="false"
          :title="`Step ${currentStep + 1} · ${stepDefs[currentStep]?.title ?? ''}`"
        >
          <Step1SelectContent
            v-show="currentStep === 0"
            :content-id="wizard.contentId"
            @update:content-id="onContentSelected"
            @begin="beginFromContent"
          />
          <Step2SelectType
            v-show="currentStep === 1"
            :engine="engine"
            @update:engine="onEngineSelected"
          />
          <Step3Script
            v-show="currentStep === 2"
            :content-id="wizard.contentId"
            :duration-s="wizard.durationS"
          />
          <Step4EngineParams
            v-show="currentStep === 3"
            :engine="engine"
            :duration-s="wizard.durationS"
            @update:duration-s="onDurationChange"
          />
          <Step5SubmitPoll
            v-show="currentStep === 4"
            :content-id="wizard.contentId"
            :duration-s="wizard.durationS"
            @done="onSubmitDone"
          />
          <Step6Preview v-show="currentStep === 5" />
        </a-card>

        <a-card :bordered="false" style="margin-top: 16px">
          <a-space>
            <a-button :disabled="currentStep === 0" @click="goPrev">
              ← 上一步
            </a-button>
            <a-tag v-if="currentStepDone" color="success">✓ 已完成</a-tag>
            <a-tag v-else color="default">⏸ 待操作</a-tag>
            <a-button
              v-if="currentStep === 0"
              type="primary"
              :disabled="!wizard.contentId"
              @click="beginFromContent"
            >
              ▶ 开始视频创作
            </a-button>
            <a-button
              v-else-if="currentStep < stepDefs.length - 1"
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
.creation-video-wizard {
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
