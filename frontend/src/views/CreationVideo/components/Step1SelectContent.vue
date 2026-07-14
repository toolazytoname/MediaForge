<script setup lang="ts">
// Step 1：选一条已有 content 作为视频创作的原文（与 video_bridge 状态
// 白名单一致：draft/gated/approved/rejected_by_human）
import { computed, onMounted, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { useContentsStore, type ContentItem } from '../../../stores'

const ALLOWED_STATUSES = new Set(['draft', 'gated', 'approved', 'rejected_by_human'])

const props = defineProps<{
  contentId: string | null
}>()

const emit = defineEmits<{
  (e: 'update:contentId', v: string | null): void
  (e: 'begin'): void
}>()

const store = useContentsStore()
const { items, loading } = storeToRefs(store)
const loadError = ref<string | null>(null)

const options = computed(() =>
  (items.value as ContentItem[])
    .filter((c) => ALLOWED_STATUSES.has(c.status))
    .map((c) => ({
      value: c.id,
      label: `${c.title}（${c.status}）`,
    })),
)

async function reload() {
  loadError.value = null
  try {
    await store.load({ limit: 100 })
  } catch (e: unknown) {
    loadError.value = e instanceof Error ? e.message : String(e)
  }
}

onMounted(reload)

function onChange(v: string | undefined) {
  emit('update:contentId', v ?? null)
}

function onBegin() {
  if (props.contentId) emit('begin')
}
</script>

<template>
  <div>
    <a-spin :spinning="loading">
      <a-empty
        v-if="!loading && options.length === 0"
        description="还没有可用内容（需 draft/gated/approved/rejected_by_human 状态），请先完成图文创作向导。"
      >
        <a-button href="/creation">去图文创作</a-button>
      </a-empty>
      <a-form v-else layout="vertical">
        <a-form-item label="内容">
          <a-select
            :value="contentId ?? undefined"
            :options="options"
            placeholder="选择一条内容作为视频原文"
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
            <a-button type="primary" :disabled="!contentId" @click="onBegin">
              ▶ 开始视频创作
            </a-button>
            <a-button @click="reload">刷新内容列表</a-button>
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
