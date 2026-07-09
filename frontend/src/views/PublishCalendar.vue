<script setup lang="ts">
// M10-8 PublishCalendar：周视图日历（按日期分桶）
import { onMounted, ref } from 'vue'
import { usePublishStore } from '../stores'
import { storeToRefs } from 'pinia'

const store = usePublishStore()
const { calendar, loading } = storeToRefs(store)

const week = ref<string | undefined>(undefined)

function load() {
  store.loadCalendar(week.value)
}
onMounted(load)
</script>

<template>
  <h2>发布日历</h2>
  <a-space style="margin-bottom: 12px">
    <a-input v-model:value="week" placeholder="YYYY-MM-DD（可选）" allow-clear style="width: 200px"
             @press-enter="load" />
    <a-button @click="load">加载</a-button>
  </a-space>
  <a-spin :spinning="loading">
    <template v-if="calendar">
      <p>
        <a :href="`/publish/calendar?week=${calendar.prev_week}`">← 上周 ({{ calendar.prev_week }})</a>
        | 本周：{{ calendar.week_start }} → {{ calendar.week_end }}
        | <a :href="`/publish/calendar?week=${calendar.next_week}`">下周 ({{ calendar.next_week }}) →</a>
      </p>
      <a-row :gutter="8">
        <a-col v-for="d in calendar.days" :key="d.date" :span="3">
          <a-card :title="d.date" size="small" style="margin-bottom: 8px">
            <a-list size="small" :data-source="d.publications" :pagination="{ pageSize: 5 }">
              <template #renderItem="{ item }">
                <a-list-item>
                  <a-tag color="purple">{{ item.platform }}</a-tag>
                  <span style="font-size: 12px">{{ item.scheduled_at.split('T')[1]?.slice(0,5) }}</span>
                  <a-tag :color="item.status === 'published' ? 'green' : 'orange'">{{ item.status }}</a-tag>
                </a-list-item>
              </template>
              <template #empty>
                <span style="color: #ccc">无</span>
              </template>
            </a-list>
          </a-card>
        </a-col>
      </a-row>
    </template>
  </a-spin>
</template>
