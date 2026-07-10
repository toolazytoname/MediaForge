<script setup lang="ts">
// M10 P2 阶段 A：图文创作页
// 左侧选题下拉（GET /api/v1/topics?status=selected&limit=50）
// 中间"一键创作"按钮（POST /api/v1/contents）
// 成功 → a-result success + 跳详情；失败 → a-alert
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import { useCreationStore, useTopicsStore } from '../stores'

const router = useRouter()
const topicsStore = useTopicsStore()
const creationStore = useCreationStore()
const { running, lastResult, lastError } = storeToRefs(creationStore)

const selectedTopicId = ref<string | undefined>(undefined)

// 加载 selected 选题列表
async function reloadTopics() {
  await topicsStore.load({ status: 'selected', limit: 50 })
}

onMounted(reloadTopics)

// 渲染下拉项
const topicOptions = computed(() =>
  topicsStore.items.map((t) => ({
    value: t.id,
    label: `${t.title} (${t.source})`,
  })),
)

async function onSubmit() {
  if (!selectedTopicId.value) return
  await creationStore.run(selectedTopicId.value)
}

function goToDetail() {
  if (lastResult.value) {
    router.push(`/contents/${lastResult.value.id}`)
  }
}

function reset() {
  creationStore.reset()
  selectedTopicId.value = undefined
}

// 当 lastResult 更新时自动滚动到结果区（通过 watch ref）
watch(lastResult, (v) => {
  if (v) {
    // 简易：把焦点移到顶部 result 区
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }
})
</script>

<template>
  <div>
    <h2>图文创作</h2>
    <p style="color: #666; margin-bottom: 16px">
      从已选中的选题（status = selected）创作 canonical 长文。
      创作过程可能耗时数分钟，取决于 LLM provider。
    </p>

    <a-spin :spinning="topicsStore.loading">
      <a-row :gutter="16">
        <a-col :span="14">
          <a-card title="选择选题">
            <a-empty
              v-if="!topicsStore.loading && topicOptions.length === 0"
              description="暂无 selected 状态的选题。先跑 score 阶段把高分 raw 推上来。"
            />
            <a-form v-else layout="vertical">
              <a-form-item label="选题">
                <a-select
                  v-model:value="selectedTopicId"
                  :options="topicOptions"
                  placeholder="选择一条 selected 选题"
                  show-search
                  :filter-option="(input: string, option: any) =>
                    (option?.label ?? '').toLowerCase().includes(input.toLowerCase())"
                  style="width: 100%"
                />
              </a-form-item>
              <a-form-item>
                <a-button
                  type="primary"
                  size="large"
                  :loading="running"
                  :disabled="!selectedTopicId || running"
                  @click="onSubmit"
                >
                  ▶ 一键创作
                </a-button>
                <a-button
                  style="margin-left: 8px"
                  :disabled="running"
                  @click="reloadTopics"
                >
                  刷新选题列表
                </a-button>
              </a-form-item>
            </a-form>
          </a-card>
        </a-col>

        <a-col :span="10">
          <a-card title="创作结果">
            <a-spin :spinning="running" tip="创作中...">
              <a-result
                v-if="lastResult"
                status="success"
                title="创作完成"
                :sub-title="`内容 ID: ${lastResult.id} · 状态: ${lastResult.status}`"
              >
                <template #extra>
                  <a-button type="primary" @click="goToDetail">
                    查看内容详情
                  </a-button>
                  <a-button style="margin-left: 8px" @click="reset">
                    再创作一条
                  </a-button>
                </template>
              </a-result>
              <a-alert
                v-else-if="lastError"
                type="error"
                :message="lastError"
                show-icon
                closable
              />
              <a-empty
                v-else
                description="选择选题后点击「一键创作」"
              />
            </a-spin>
          </a-card>
        </a-col>
      </a-row>
    </a-spin>
  </div>
</template>
