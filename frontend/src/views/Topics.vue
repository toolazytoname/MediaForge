<script setup lang="ts">
// M10-8 Topics：选题池（列表 + 状态/支柱/源筛选 + 分页）
// M10 P2 阶段 C：promote / reject 按钮解 disabled，调 POST /api/v1/topics/{id}/{action}
import { onMounted, ref } from 'vue'
import { useTopicsStore, useTopicActionStore } from '../stores'
import { storeToRefs } from 'pinia'

const store = useTopicsStore()
const actionStore = useTopicActionStore()
const { items, total, loading } = storeToRefs(store)

const filters = ref<{ status?: string; pillar?: string; source?: string }>({})
const page = ref({ current: 1, pageSize: 20 })

const success = ref<string | null>(null)
const errorAlert = ref<{ code: string; msg: string; topicId: string } | null>(null)

function reload() {
  const offset = (page.value.current - 1) * page.value.pageSize
  store.load({ ...filters.value, limit: page.value.pageSize, offset })
}

onMounted(reload)

async function onAction(topicId: string, action: 'promote' | 'reject') {
  success.value = null
  errorAlert.value = null
  const r = await actionStore.run(topicId, action)
  if (r) {
    success.value = `已 ${action}: ${r.id} → ${r.status}`
    reload()
  } else {
    const [code, ...rest] = (actionStore.lastError ?? '').split(':')
    errorAlert.value = {
      code: code ?? 'unknown',
      msg: rest.join(':').trim(),
      topicId,
    }
  }
}
</script>

<template>
  <h2>选题池</h2>
  <a-space style="margin-bottom: 12px">
    <a-select v-model:value="filters.status" placeholder="status" allow-clear style="width: 140px"
              @change="reload">
      <a-select-option value="raw">raw</a-select-option>
      <a-select-option value="scored">scored</a-select-option>
      <a-select-option value="selected">selected</a-select-option>
      <a-select-option value="consumed">consumed</a-select-option>
      <a-select-option value="rejected">rejected</a-select-option>
    </a-select>
    <a-input v-model:value="filters.pillar" placeholder="pillar" allow-clear style="width: 140px"
             @press-enter="reload" />
    <a-input v-model:value="filters.source" placeholder="source" allow-clear style="width: 140px"
             @press-enter="reload" />
    <a-button @click="reload">刷新</a-button>
    <a-button disabled>+ 新增选题（P2）</a-button>
  </a-space>
  <a-alert
    v-if="success"
    type="success"
    :message="success"
    show-icon
    closable
    style="margin-bottom: 12px"
    @close="success = null"
  />
  <a-alert
    v-if="errorAlert"
    type="error"
    :message="`操作失败 (${errorAlert.topicId}): ${errorAlert.code} - ${errorAlert.msg}`"
    show-icon
    closable
    style="margin-bottom: 12px"
    @close="errorAlert = null"
  />
  <a-spin :spinning="loading">
    <a-table
      :data-source="items"
      :columns="[
        { title: 'id', dataIndex: 'id', width: 120 },
        { title: 'title', dataIndex: 'title' },
        { title: 'source', dataIndex: 'source', width: 100 },
        { title: 'pillar', dataIndex: 'pillar', width: 100 },
        { title: 'score', dataIndex: 'score', width: 80 },
        { title: 'status', dataIndex: 'status', width: 100 },
        { title: 'updated_at', dataIndex: 'updated_at', width: 200 },
        { title: '操作', key: 'op' },
      ]"
      :pagination="{
        current: page.current,
        pageSize: page.pageSize,
        total: total,
        showSizeChanger: true,
        onChange: (c: number, s: number) => { page = { current: c, pageSize: s }; reload() },
      }"
      row-key="id"
      size="small"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'op'">
          <a-button
            size="small"
            :loading="actionStore.running"
            :disabled="record.status !== 'scored'"
            @click="onAction(record.id, 'promote')"
          >
            promote
          </a-button>
          <a-button
            size="small"
            danger
            :loading="actionStore.running"
            :disabled="record.status !== 'scored'"
            style="margin-left: 4px"
            @click="onAction(record.id, 'reject')"
          >
            reject
          </a-button>
        </template>
      </template>
    </a-table>
  </a-spin>
</template>