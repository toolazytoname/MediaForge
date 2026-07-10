<script setup lang="ts">
// Step 1：选选题（从 selected 状态列表里选一条）
import { computed, onMounted, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { useTopicsStore, type TopicItem } from '../../../stores'

const props = defineProps<{
  selectedTopicId: string | null
}>()

const emit = defineEmits<{
  (e: 'update:selectedTopicId', v: string | null): void
  (e: 'begin'): void
}>()

const store = useTopicsStore()
const { items, loading } = storeToRefs(store)
const loadError = ref<string | null>(null)

const options = computed(() =>
  (items.value as TopicItem[]).map((t) => ({
    value: t.id,
    label: t.title + (t.pillar ? ` · ${t.pillar}` : ''),
  })),
)

async function reload() {
  loadError.value = null
  try {
    await store.load({ status: 'selected', limit: 50 })
  } catch (e: unknown) {
    loadError.value = e instanceof Error ? e.message : String(e)
  }
}

onMounted(reload)

function onChange(v: string | undefined) {
  emit('update:selectedTopicId', v ?? null)
}

function onBegin() {
  if (props.selectedTopicId) emit('begin')
}
</script>

<template>
  <div>
    <a-spin :spinning="loading">
      <a-empty
        v-if="!loading && items.length === 0"
        description="暂无 selected 状态的选题。先跑 score / create 阶段把高分内容推上来。"
      />
      <a-form v-else layout="vertical">
        <a-form-item label="选题">
          <a-select
            :value="selectedTopicId ?? undefined"
            :options="options"
            placeholder="选择一条 selected 选题"
            show-search
            allow-clear
            style="width: 100%"
            :filter-option="(input: string, option: { label?: string } | undefined) =>
              (option?.label ?? '').toLowerCase().includes(input.toLowerCase())"
            @change="onChange"
          />
        </a-form-item>
        <a-form-item>
          <a-space>
            <a-button
              type="primary"
              :disabled="!selectedTopicId"
              @click="onBegin"
            >
              ▶ 开始创作
            </a-button>
            <a-button @click="reload">刷新选题列表</a-button>
          </a-space>
        </a-form-item>
      </a-form>
    </a-spin>
    <a-alert
      v-if="loadError"
      type="error"
      :message="`加载失败: ${loadError}`"
      show-icon
      closable
      style="margin-top: 12px"
      @close="loadError = null"
    />
  </div>
</template>
