<script setup lang="ts">
// M10-8 PublishCalendar：周视图日历（按日期分桶）
// M10 P2 阶段 C：reschedule / cancel / retry 按钮解 disabled
//   - reschedule: 弹 a-modal 改 scheduled_at → POST /api/v1/publications/{id}/reschedule
//   - cancel: POST /api/v1/publications/{id}/cancel
//   - retry: POST /api/v1/publications/{id}/retry
import { onMounted, ref } from 'vue'
import { usePublishStore, usePubActionStore } from '../stores'
import { storeToRefs } from 'pinia'

const store = usePublishStore()
const actionStore = usePubActionStore()
const { calendar, loading } = storeToRefs(store)

const week = ref<string | undefined>(undefined)

const success = ref<string | null>(null)
const errorAlert = ref<{ code: string; msg: string } | null>(null)

// reschedule modal state
const modalOpen = ref(false)
const modalPubId = ref<string | null>(null)
const modalNewTime = ref<string>('2026-07-12T18:30:00+00:00')

function load() {
  store.loadCalendar(week.value)
}
onMounted(load)

async function onCancel(pubId: string) {
  success.value = null
  errorAlert.value = null
  const r = await actionStore.cancel(pubId)
  if (r) {
    success.value = `已 cancel: ${r.id} → ${r.status}`
    load()
  } else {
    showError()
  }
}

async function onRetry(pubId: string) {
  success.value = null
  errorAlert.value = null
  const r = await actionStore.retry(pubId)
  if (r) {
    success.value = `已 retry: ${r.id} → ${r.status}`
    load()
  } else {
    showError()
  }
}

function openReschedule(pubId: string, currentTime: string) {
  modalPubId.value = pubId
  modalNewTime.value = currentTime
  modalOpen.value = true
}

async function onRescheduleSubmit() {
  if (!modalPubId.value) return
  const pubId = modalPubId.value
  success.value = null
  errorAlert.value = null
  const r = await actionStore.reschedule(pubId, modalNewTime.value)
  modalOpen.value = false
  modalPubId.value = null
  if (r) {
    success.value = `已 reschedule: ${r.id} → ${r.scheduled_at}`
    load()
  } else {
    showError()
  }
}

function showError() {
  const [code, ...rest] = (actionStore.lastError ?? '').split(':')
  errorAlert.value = {
    code: code ?? 'unknown',
    msg: rest.join(':').trim(),
  }
}

function isQueued(item: any): boolean {
  return item.status === 'queued'
}
function isFailed(item: any): boolean {
  return item.status === 'failed'
}
</script>

<template>
  <h2>发布日历</h2>
  <a-space style="margin-bottom: 12px">
    <a-input v-model:value="week" placeholder="YYYY-MM-DD（可选）" allow-clear style="width: 200px"
             @press-enter="load" />
    <a-button @click="load">加载</a-button>
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
    :message="`操作失败: ${errorAlert.code} - ${errorAlert.msg}`"
    show-icon
    closable
    style="margin-bottom: 12px"
    @close="errorAlert = null"
  />
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
                  <div v-if="isQueued(item) || isFailed(item)" style="margin-top: 4px">
                    <a-button
                      v-if="isQueued(item)"
                      size="small"
                      :loading="actionStore.running"
                      @click="openReschedule(item.id, item.scheduled_at)"
                    >
                      reschedule
                    </a-button>
                    <a-button
                      v-if="isQueued(item)"
                      size="small"
                      danger
                      :loading="actionStore.running"
                      style="margin-left: 4px"
                      @click="onCancel(item.id)"
                    >
                      cancel
                    </a-button>
                    <a-button
                      v-if="isFailed(item)"
                      size="small"
                      type="primary"
                      :loading="actionStore.running"
                      @click="onRetry(item.id)"
                    >
                      retry
                    </a-button>
                  </div>
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

  <a-modal
    v-model:open="modalOpen"
    title="改排期时间"
    @ok="onRescheduleSubmit"
    :ok-button-props="{ loading: actionStore.running }"
  >
    <a-form layout="vertical">
      <a-form-item label="新的 scheduled_at (ISO8601 UTC)">
        <a-input v-model:value="modalNewTime" placeholder="2026-07-12T18:30:00+00:00" />
      </a-form-item>
      <p style="color: #888; font-size: 12px">
        publication: {{ modalPubId }}
      </p>
    </a-form>
  </a-modal>
</template>