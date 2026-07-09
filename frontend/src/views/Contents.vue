<script setup lang="ts">
// M10-8 Contents：内容库（列表 + 状态/支柱筛选 + 分页 + 详情链接）
import { onMounted, ref } from 'vue'
import { useContentsStore } from '../stores'
import { storeToRefs } from 'pinia'

const store = useContentsStore()
const { items, total, loading } = storeToRefs(store)
const filters = ref<{ status?: string; pillar?: string }>({})
const page = ref({ current: 1, pageSize: 20 })

function reload() {
  const offset = (page.value.current - 1) * page.value.pageSize
  store.load({ ...filters.value, limit: page.value.pageSize, offset })
}
onMounted(reload)
</script>

<template>
  <h2>内容库</h2>
  <a-space style="margin-bottom: 12px">
    <a-select v-model:value="filters.status" placeholder="status" allow-clear style="width: 140px"
              @change="reload">
      <a-select-option value="draft">draft</a-select-option>
      <a-select-option value="gated">gated</a-select-option>
      <a-select-option value="approved">approved</a-select-option>
      <a-select-option value="rejected_by_human">rejected_by_human</a-select-option>
      <a-select-option value="discarded">discarded</a-select-option>
      <a-select-option value="failed">failed</a-select-option>
      <a-select-option value="done">done</a-select-option>
    </a-select>
    <a-input v-model:value="filters.pillar" placeholder="pillar" allow-clear style="width: 140px"
             @press-enter="reload" />
    <a-button @click="reload">刷新</a-button>
  </a-space>
  <a-spin :spinning="loading">
    <a-table
      :data-source="items"
      :columns="[
        { title: 'id', dataIndex: 'id', width: 120 },
        { title: 'title', dataIndex: 'title' },
        { title: 'pillar', dataIndex: 'pillar', width: 100 },
        { title: 'formats', dataIndex: 'formats', width: 200 },
        { title: 'gate_score', dataIndex: 'gate_score_total', width: 100 },
        { title: 'status', dataIndex: 'status', width: 130 },
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
        <template v-if="column.dataIndex === 'id'">
          <router-link :to="`/contents/${record.id}`">{{ record.id }}</router-link>
        </template>
        <template v-else-if="column.dataIndex === 'formats'">
          <a-tag v-for="f in record.formats" :key="f" color="blue">{{ f }}</a-tag>
        </template>
      </template>
    </a-table>
  </a-spin>
</template>
