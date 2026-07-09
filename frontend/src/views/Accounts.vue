<script setup lang="ts">
// M10-8 Accounts：账号 + cookie 健康 + 登录引导
import { onMounted } from 'vue'
import { useAccountsStore } from '../stores'
import { storeToRefs } from 'pinia'

const store = useAccountsStore()
const { items, guidance, loading } = storeToRefs(store)
onMounted(() => store.load())
</script>

<template>
  <h2>账号管理</h2>
  <a-spin :spinning="loading">
    <a-card title="Cookie 健康" style="margin-bottom: 16px">
      <a-table
        :data-source="items"
        :columns="[
          { title: 'platform', dataIndex: 'platform', width: 120 },
          { title: 'account', dataIndex: 'account', width: 120 },
          { title: 'healthy', dataIndex: 'healthy', width: 80 },
          { title: 'detail', dataIndex: 'detail' },
          { title: 'last_check_at', dataIndex: 'last_check_at', width: 200 },
        ]"
        :pagination="false"
        size="small"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.dataIndex === 'healthy'">
            <a-tag :color="record.healthy ? 'green' : 'red'">
              {{ record.healthy ? '✓' : '✗' }}
            </a-tag>
          </template>
        </template>
      </a-table>
    </a-card>

    <a-card title="登录引导">
      <a-list size="small" :data-source="guidance">
        <template #renderItem="{ item }">
          <a-list-item>
            <a-tag color="blue">{{ item.platform }}</a-tag>
            <code style="margin: 0 8px">{{ item.command }}</code>
            <span style="color: #666">{{ item.notes }}</span>
          </a-list-item>
        </template>
      </a-list>
    </a-card>
  </a-spin>
</template>
