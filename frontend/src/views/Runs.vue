<script setup lang="ts">
// M10-8 Runs：运行台（白名单 + 历史，P1 历史空，触发按钮禁用 P2）
import { onMounted } from 'vue'
import { useRunsStore } from '../stores'
import { storeToRefs } from 'pinia'

const store = useRunsStore()
const { items, whitelist, loading } = storeToRefs(store)
onMounted(() => store.load())
</script>

<template>
  <h2>运行台</h2>
  <a-alert
    type="info"
    message="写操作阶段留 P2 — 当前 P1 仅展示白名单"
    show-icon
    style="margin-bottom: 16px"
  />
  <a-spin :spinning="loading">
    <a-card title="允许的 stage（白名单）" style="margin-bottom: 16px">
      <a-space wrap>
        <a-button v-for="s in whitelist" :key="s" disabled>{{ s }}（P2 启用）</a-button>
      </a-space>
    </a-card>
    <a-card title="运行历史">
      <a-empty v-if="items.length === 0" description="暂无历史 — runner_bridge 留 P2" />
      <a-table v-else :data-source="items" :columns="[]" :pagination="false" size="small" />
    </a-card>
  </a-spin>
</template>
