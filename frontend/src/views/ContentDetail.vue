<script setup lang="ts">
// M10-8 ContentDetail：内容详情（canonical HTML + 派生文件 + 图卡 + 出版时间线）
// M10 P2 阶段 B：加「图文衍生」card，含 2 个按钮（衍生小红书 / 真实 AI 出图）
// M10-11 阶段 D：approved 内容显示「手动排期」card
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  useContentsStore,
  useDerivativeStore,
  useImageGenStore,
  useScheduleStore,
  useAccountsStore,
  type ContentDetail,
} from '../stores'

const route = useRoute()
const router = useRouter()
const store = useContentsStore()
const derivStore = useDerivativeStore()
const imgStore = useImageGenStore()
const schedStore = useScheduleStore()
const accountsStore = useAccountsStore()
const data = ref<ContentDetail | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)

// 「图文衍生」区块本地态
const derivSuccess = ref<string | null>(null)  // "已生成 N 张 slides"
const imgSuccess = ref<string | null>(null)    // "cover + N inline, $X"
const derivErrorAlert = ref<{ msg: string; code: string } | null>(null)
const imgErrorAlert = ref<{ msg: string; code: string } | null>(null)

// 「手动排期」区块本地态（M10-11）
const schedPlatform = ref<string | undefined>(undefined)
const schedAccountId = ref<string | undefined>(undefined)
const schedDatetime = ref<string | undefined>(undefined)
const schedSuccess = ref<string | null>(null)
const schedErrorAlert = ref<{ msg: string; code: string } | null>(null)

// 已发出去别再改（done / published 不允许改）
const derivDisabled = computed(() => {
  if (!data.value) return true
  return data.value.status === 'done'
})

// 已批准或待审的可手动排期（仅在 approved / gated 时显示表单）
const schedEligible = computed(() => {
  if (!data.value) return false
  return data.value.status === 'approved' || data.value.status === 'gated'
})

// 平台选项（去重自 accountsStore.items；不硬编码）
const platformOptions = computed(() => {
  const set = new Set<string>()
  for (const it of accountsStore.items) {
    if (it.platform) set.add(it.platform)
  }
  return Array.from(set).sort()
})

// 当前平台下的账号选项
const accountOptions = computed(() => {
  if (!schedPlatform.value) return []
  return accountsStore.items
    .filter((it) => it.platform === schedPlatform.value)
    .map((it) => it.account)
})

// 表单齐全才能提交
const schedCanSubmit = computed(() => {
  return Boolean(
    schedPlatform.value
      && schedAccountId.value
      && schedDatetime.value,
  )
})

async function refresh() {
  try {
    data.value = await store.getDetail(route.params.id as string)
  } catch (e) {
    error.value = String(e)
  }
}

onMounted(async () => {
  loading.value = true
  error.value = null
  try {
    data.value = await store.getDetail(route.params.id as string)
    // 加载账号列表（驱动平台/账号下拉）——空数据也不阻塞
    if (accountsStore.items.length === 0) {
      await accountsStore.load().catch(() => null)
    }
  } catch (e) {
    error.value = String(e)
  } finally {
    loading.value = false
  }
})

async function onDerive() {
  if (!data.value) return
  derivSuccess.value = null
  derivErrorAlert.value = null
  const r = await derivStore.run(data.value.id)
  if (r) {
    derivSuccess.value = `已衍生小红书：${r.slides_count} 张 slides，caption ${r.caption_chars} 字，${r.tags.length} 个 tags`
    await refresh()
  } else {
    const [code, ...rest] = (derivStore.lastError ?? '').split(':')
    derivErrorAlert.value = { code: code ?? 'unknown', msg: rest.join(':').trim() }
  }
}

async function onGenerateImages() {
  if (!data.value) return
  imgSuccess.value = null
  imgErrorAlert.value = null
  const r = await imgStore.run(data.value.id)
  if (r) {
    imgSuccess.value = `已出图：cover + ${r.inline_images.length} inline，$${r.cost_usd.toFixed(4)}`
    await refresh()
  } else {
    const [code, ...rest] = (imgStore.lastError ?? '').split(':')
    imgErrorAlert.value = { code: code ?? 'unknown', msg: rest.join(':').trim() }
  }
}

async function onSchedule() {
  if (!data.value || !schedCanSubmit.value) return
  schedSuccess.value = null
  schedErrorAlert.value = null
  // datetime-local 给的是「本地时间无时区」，但我们收集的就是当前浏览器所在时区的字面值，
  // 后端接受 ISO8601（带时区）会正常解析为 UTC。直接传字面值即可。
  const isoLocal = schedDatetime.value!
  const r = await schedStore.run(data.value.id, {
    platform: schedPlatform.value!,
    account_id: schedAccountId.value!,
    scheduled_at: isoLocal,
  })
  if (r) {
    schedSuccess.value = `已加入排期：${r.platform} / ${r.account_id} @ ${r.scheduled_at}`
    await refresh()
  } else {
    const [code, ...rest] = (schedStore.lastError ?? '').split(':')
    schedErrorAlert.value = { code: code ?? 'unknown', msg: rest.join(':').trim() }
  }
}

function goSettings() {
  router.push('/settings')
}
</script>

<template>
  <a-spin :spinning="loading">
    <a-alert v-if="error" type="error" :message="error" show-icon style="margin-bottom: 16px" />
    <template v-if="data">
      <h2>{{ data.title }}</h2>
      <a-descriptions :column="2" size="small" bordered style="margin-bottom: 16px">
        <a-descriptions-item label="id">{{ data.id }}</a-descriptions-item>
        <a-descriptions-item label="status">
          <a-tag>{{ data.status }}</a-tag>
        </a-descriptions-item>
        <a-descriptions-item label="pillar">{{ data.pillar }}</a-descriptions-item>
        <a-descriptions-item label="formats">
          <a-tag v-for="f in data.formats" :key="f" color="blue">{{ f }}</a-tag>
        </a-descriptions-item>
        <a-descriptions-item v-if="data.gate_score_total" label="门禁分">
          {{ data.gate_score_total }}
        </a-descriptions-item>
        <a-descriptions-item v-if="data.gate_verdict" label="verdict">
          {{ data.gate_verdict }}
        </a-descriptions-item>
      </a-descriptions>

      <a-row :gutter="16">
        <a-col :span="14">
          <a-card title="canonical 预览">
            <div v-html="data.canonical_html" class="md-body" />
            <p v-if="!data.canonical_html" style="color: #999">（无 canonical.md 或为空）</p>
          </a-card>
        </a-col>
        <a-col :span="10">
          <!-- 阶段 B：图文衍生 + AI 出图 -->
          <a-card title="图文衍生" style="margin-bottom: 16px">
            <a-space direction="vertical" style="width: 100%">
              <a-button
                type="primary"
                :loading="derivStore.running"
                :disabled="derivDisabled"
                block
                @click="onDerive"
              >
                ▶ 衍生小红书
              </a-button>
              <a-button
                :loading="imgStore.running"
                :disabled="derivDisabled"
                block
                @click="onGenerateImages"
              >
                ▶ 真实 AI 出图
              </a-button>
              <a-alert
                v-if="derivSuccess"
                type="success"
                :message="derivSuccess"
                show-icon
                closable
                @close="derivSuccess = null"
              />
              <a-alert
                v-if="imgSuccess"
                type="success"
                :message="imgSuccess"
                show-icon
                closable
                @close="imgSuccess = null"
              />
              <a-alert
                v-if="derivErrorAlert"
                type="error"
                :message="`衍生失败: ${derivErrorAlert.code} - ${derivErrorAlert.msg}`"
                show-icon
                closable
                @close="derivErrorAlert = null"
              />
              <a-alert
                v-if="imgErrorAlert"
                type="error"
                :message="`出图失败: ${imgErrorAlert.code} - ${imgErrorAlert.msg}`"
                show-icon
                closable
                @close="imgErrorAlert = null"
              >
                <template #description>
                  <div>{{ imgErrorAlert.code }}: {{ imgErrorAlert.msg }}</div>
                  <a-button
                    v-if="imgErrorAlert.code === 'image_provider_unavailable'"
                    size="small"
                    type="link"
                    @click="goSettings"
                  >
                    前往设置 →
                  </a-button>
                  <div v-if="imgErrorAlert.code === 'image_provider_unavailable'" style="color: #888; font-size: 12px; margin-top: 4px">
                    AI 出图需配置 image provider key（MiniMax / Agnes-AI），详见 /settings
                  </div>
                </template>
              </a-alert>
            </a-space>
          </a-card>

          <!-- 阶段 D：手动排期（M10-11） -->
          <a-card v-if="schedEligible" title="手动排期" style="margin-bottom: 16px">
            <a-space direction="vertical" style="width: 100%">
              <a-select
                v-model:value="schedPlatform"
                placeholder="选平台"
                :options="platformOptions.map(p => ({ value: p, label: p }))"
                allow-clear
                @change="schedAccountId = undefined"
              />
              <a-select
                v-model:value="schedAccountId"
                placeholder="选账号"
                :options="accountOptions.map(a => ({ value: a, label: a }))"
                :disabled="!schedPlatform"
                allow-clear
              />
              <a-input
                v-model:value="schedDatetime"
                type="datetime-local"
                placeholder="选时间"
              />
              <a-button
                type="primary"
                :loading="schedStore.running"
                :disabled="!schedCanSubmit"
                block
                @click="onSchedule"
              >
                ▶ 加入排期
              </a-button>
              <a-alert
                v-if="schedSuccess"
                type="success"
                :message="schedSuccess"
                show-icon
                closable
                @close="schedSuccess = null"
              />
              <a-alert
                v-if="schedErrorAlert"
                type="error"
                :message="`排期失败: ${schedErrorAlert.code} - ${schedErrorAlert.msg}`"
                show-icon
                closable
                @close="schedErrorAlert = null"
              />
              <div v-if="platformOptions.length === 0" style="color: #888; font-size: 12px">
                未配置任何平台账号，<a @click="goSettings">前往设置 →</a>
              </div>
            </a-space>
          </a-card>

          <a-card title="派生文件 + 图卡" style="margin-bottom: 16px">
            <a-list size="small" :data-source="data.files" :pagination="{ pageSize: 8 }">
              <template #renderItem="{ item }">
                <a-list-item>
                  <a-tag v-if="item.platform" color="purple">{{ item.platform }}</a-tag>
                  <a-tag color="default">{{ item.kind }}</a-tag>
                  <span style="margin-left: 8px">{{ item.path }}</span>
                  <a-tag v-if="!item.exists" color="red" style="margin-left: 8px">缺</a-tag>
                  <a-tag v-else color="green" style="margin-left: 8px">{{ item.size }}B</a-tag>
                </a-list-item>
              </template>
            </a-list>
          </a-card>

          <a-card v-if="data.images.cover || data.images.inline.length" title="图卡预览" style="margin-bottom: 16px">
            <a-image v-if="data.images.cover" :src="data.images.cover" :width="200" />
            <div v-if="data.images.inline.length" style="margin-top: 8px; display: flex; gap: 8px; flex-wrap: wrap">
              <a-image v-for="(u, i) in data.images.inline" :key="i" :src="u" :width="100" />
            </div>
          </a-card>

          <a-card title="排期（publications）">
            <a-list size="small" :data-source="data.publications">
              <template #renderItem="{ item }">
                <a-list-item>
                  <a-tag color="blue">{{ item.platform }}</a-tag>
                  <span style="margin-left: 8px">{{ item.scheduled_at }}</span>
                  <a-tag style="margin-left: 8px">{{ item.status }}</a-tag>
                </a-list-item>
              </template>
              <template #empty>
                <span style="color: #999">无排期</span>
              </template>
            </a-list>
          </a-card>
        </a-col>
      </a-row>
    </template>
  </a-spin>
</template>

<style scoped>
.md-body :deep(h1) { font-size: 24px; margin: 16px 0 8px; }
.md-body :deep(h2) { font-size: 20px; margin: 14px 0 6px; }
.md-body :deep(p) { line-height: 1.7; margin: 8px 0; }
.md-body :deep(code) { background: #f5f5f5; padding: 2px 4px; border-radius: 2px; }
</style>
