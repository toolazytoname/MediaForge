<script setup lang="ts">
// Step 5：排期（POST /api/v1/contents/{id}/schedule）
// 平台/账号从 useAccountsStore.items 动态读取（不硬编码）
import { computed, onMounted, ref } from 'vue'
import {
  useAccountsStore,
  useScheduleStore,
  type AccountHealthItem,
  type PublicationItem,
  type SchedulePayload,
} from '../../../stores'

const props = defineProps<{
  contentId: string | null
}>()

const emit = defineEmits<{
  (e: 'scheduled', pub: PublicationItem): void
}>()

const store = useScheduleStore()
const accountsStore = useAccountsStore()

const platform = ref<string | undefined>(undefined)
const accountId = ref<string | undefined>(undefined)
const scheduledAt = ref<string | undefined>(undefined)
const successMsg = ref<string | null>(null)
const localError = ref<string | null>(null)

function splitError(err: string | null): { code: string; msg: string } {
  if (!err) return { code: 'unknown', msg: '' }
  const [code, ...rest] = err.split(':')
  return { code: (code ?? 'unknown').trim(), msg: rest.join(':').trim() }
}

// 平台/账号从 cfg 账号列表动态推导（禁止硬编码）
const platformOptions = computed(() => {
  const set = new Set<string>()
  for (const it of accountsStore.items as AccountHealthItem[]) {
    if (it.platform) set.add(it.platform)
  }
  return Array.from(set).sort().map((p) => ({ value: p, label: p }))
})

const accountOptions = computed(() => {
  if (!platform.value) return []
  return (accountsStore.items as AccountHealthItem[])
    .filter((it) => it.platform === platform.value)
    .map((it) => ({ value: it.account, label: it.account }))
})

const canSubmit = computed(() =>
  Boolean(platform.value && accountId.value && scheduledAt.value),
)

const errorInfo = computed(() => splitError(localError.value))

onMounted(() => {
  if (accountsStore.items.length === 0) {
    accountsStore.load().catch(() => null)
  }
})

async function onSchedule() {
  if (!props.contentId || !canSubmit.value) return
  successMsg.value = null
  localError.value = null
  const payload: SchedulePayload = {
    platform: platform.value as string,
    account_id: accountId.value as string,
    scheduled_at: scheduledAt.value as string,
  }
  const r = await store.run(props.contentId, payload)
  if (r) {
    successMsg.value = `已加入排期：${r.platform} / ${r.account_id} @ ${r.scheduled_at}`
    emit('scheduled', r)
  } else {
    localError.value = store.lastError ?? '未知错误'
  }
}
</script>

<template>
  <div>
    <a-empty v-if="!contentId" description="请先完成 Step 2 创建内容" />
    <a-form v-else layout="vertical">
      <a-form-item label="平台">
        <a-select v-model:value="platform" :options="platformOptions" placeholder="选平台" allow-clear
          @change="accountId = undefined" />
      </a-form-item>
      <a-form-item label="账号">
        <a-select v-model:value="accountId" :options="accountOptions" :disabled="!platform"
          placeholder="选账号" allow-clear />
      </a-form-item>
      <a-form-item label="时间">
        <a-input v-model:value="scheduledAt" type="datetime-local" placeholder="选时间" />
      </a-form-item>
      <a-form-item>
        <a-button type="primary" size="large" :loading="store.running" :disabled="!canSubmit || store.running"
          @click="onSchedule">
          ▶ 加入排期
        </a-button>
      </a-form-item>
      <div v-if="platformOptions.length === 0 && !accountsStore.loading"
        style="color: #888; font-size: 12px; margin-top: 8px">
        未配置任何平台账号，请前往 /settings 配置。
      </div>
      <a-alert v-if="successMsg" type="success" :message="successMsg" show-icon closable
        style="margin-top: 12px" @close="successMsg = null" />
      <a-alert v-if="localError" type="error" :message="`排期失败: ${errorInfo.code} - ${errorInfo.msg}`"
        show-icon closable style="margin-top: 12px" @close="localError = null" />
    </a-form>
  </div>
</template>
