<script setup lang="ts">
// M10-8 PublishRecords：发布记录列表（含可选 metric）
import { onMounted, ref } from 'vue'
import { usePublishStore } from '../stores'
import { storeToRefs } from 'pinia'

const store = usePublishStore()
const { records, loading } = storeToRefs(store)
const filters = ref<{ status?: string; platform?: string; with_metric?: boolean }>({ with_metric: true })

function reload() {
  store.loadRecords({ ...filters.value })
}
onMounted(reload)
</script>

<template>
  <h2>发布记录</h2>
  <a-space style="margin-bottom: 12px">
    <a-select v-model:value="filters.status" placeholder="status" allow-clear style="width: 140px" @change="reload">
      <a-select-option value="queued">queued</a-select-option>
      <a-select-option value="publishing">publishing</a-select-option>
      <a-select-option value="published">published</a-select-option>
      <a-select-option value="failed">failed</a-select-option>
      <a-select-option value="cancelled">cancelled</a-select-option>
    </a-select>
    <a-input v-model:value="filters.platform" placeholder="platform" allow-clear style="width: 140px" @press-enter="reload" />
    <a-checkbox v-model:checked="filters.with_metric" @change="reload">含最新 metric</a-checkbox>
    <a-button @click="reload">刷新</a-button>
  </a-space>
  <a-spin :spinning="loading">
    <a-table
      :data-source="records"
      :columns="[
        { title: 'id', dataIndex: 'id', width: 120 },
        { title: 'platform', dataIndex: 'platform', width: 100 },
        { title: 'account', dataIndex: 'account_id', width: 100 },
        { title: 'scheduled_at', dataIndex: 'scheduled_at', width: 200 },
        { title: 'status', dataIndex: 'status', width: 100 },
        { title: 'views', key: 'views', width: 80 },
        { title: 'likes', key: 'likes', width: 80 },
        { title: 'error', dataIndex: 'error' },
      ]"
      :pagination="{ pageSize: 50 }"
      row-key="id"
      size="small"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'views'">
          {{ record.latest_metric?.views ?? '—' }}
        </template>
        <template v-else-if="column.key === 'likes'">
          {{ record.latest_metric?.likes ?? '—' }}
        </template>
      </template>
    </a-table>
  </a-spin>
</template>
