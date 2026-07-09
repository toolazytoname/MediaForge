<script setup lang="ts">
// M10-8 Settings：脱敏 config + doctor 报告
import { onMounted } from 'vue'
import { useSettingsStore } from '../stores'
import { storeToRefs } from 'pinia'

const store = useSettingsStore()
const { config, doctor, loading } = storeToRefs(store)
onMounted(() => store.load())
</script>

<template>
  <h2>设置</h2>
  <a-spin :spinning="loading">
    <a-card title="Doctor 体检" style="margin-bottom: 16px">
      <a-list size="small" :data-source="doctor">
        <template #renderItem="{ item }">
          <a-list-item>
            <a-tag :color="item.ok ? 'green' : 'red'">{{ item.ok ? '✓' : '✗' }}</a-tag>
            <strong style="margin-left: 8px">{{ item.name }}</strong>
            <span style="margin-left: 8px; color: #666">{{ item.hint }}</span>
          </a-list-item>
        </template>
        <template #empty>
          <span style="color: #999">无 doctor 报告</span>
        </template>
      </a-list>
    </a-card>

    <a-card title="Config（脱敏展示）">
      <pre v-if="config" style="background: #f5f5f5; padding: 12px; border-radius: 4px; overflow: auto; max-height: 500px">{{ JSON.stringify(config, null, 2) }}</pre>
      <a-empty v-else description="无 config" />
    </a-card>
  </a-spin>
</template>
