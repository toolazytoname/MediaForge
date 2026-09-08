<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { Modal } from 'ant-design-vue'
import { storeToRefs } from 'pinia'
import { useSettingsStore, type ByokCheckResult, type WechatCheckResult } from '../stores'

const store = useSettingsStore()
const { config, doctor, keyGroups, textProvider, loading } = storeToRefs(store)

const pendingValues = reactive<Record<string, string>>({})
const saving = reactive<Record<string, boolean>>({})
const byokForm = reactive({
  base_url: '',
  text_model: '',
  image_model: '',
  wire_api: 'responses' as 'responses' | 'chat_completions',
  api_key: '',
  input_price: null as number | null,
  output_price: null as number | null,
})
const byokMeta = ref({
  key_set: false,
  masked: null as string | null,
  priced: false,
  price_source: 'unpriced',
  cost_kind: 'estimate',
  reload_error: null as string | null,
})
const byokBusy = ref(false)
const byokCheck = ref<ByokCheckResult | null>(null)
const wechatForm = reactive({ app_id: '', app_secret: '' })
const wechatMeta = ref({
  configured: false,
  delivery_enabled: false,
  credentials_path: '',
  warning: null as string | null,
})
const wechatBusy = ref(false)
const wechatCheck = ref<WechatCheckResult | null>(null)
const publishEnabledSaving = ref(false)
const platformsSaving = ref(false)
const pendingPlatforms = ref<string[]>([])

const publishEnabled = computed(() => config.value?.publish?.enabled === true)
const knownPlatforms = computed(() => Object.keys(config.value?.platforms ?? {}))

onMounted(async () => {
  await Promise.all([store.load(), store.loadKeys(), loadByokForm(), loadWechatForm()])
})

watch(config, (c) => {
  pendingPlatforms.value = [...((c?.publish?.allowed_platforms as string[]) ?? [])]
}, { immediate: true })

async function loadByokForm(): Promise<void> {
  const data = await store.loadByok()
  if (!data) return
  byokForm.base_url = data.base_url
  byokForm.text_model = data.text_model
  byokForm.image_model = data.image_model
  byokForm.wire_api = data.wire_api
  byokForm.api_key = ''
  byokForm.input_price = data.input_price
  byokForm.output_price = data.output_price
  byokMeta.value = {
    key_set: data.key_set,
    masked: data.masked,
    priced: data.priced,
    price_source: data.price_source,
    cost_kind: data.cost_kind,
    reload_error: data.reload_error ?? null,
  }
}

async function loadWechatForm(): Promise<void> {
  const data = await store.loadWechat()
  if (!data) return
  wechatForm.app_id = data.app_id
  wechatForm.app_secret = ''
  wechatMeta.value = {
    configured: data.configured,
    delivery_enabled: data.delivery_enabled,
    credentials_path: data.credentials_path,
    warning: data.warning,
  }
}

async function saveByok(): Promise<void> {
  byokBusy.value = true
  try {
    const ok = await store.saveByok({
      ...byokForm,
      api_key: byokForm.api_key.trim() || undefined,
    })
    if (ok) {
      byokForm.api_key = ''
      await loadByokForm()
    }
  } finally {
    byokBusy.value = false
  }
}

async function checkByok(): Promise<void> {
  byokBusy.value = true
  try {
    byokCheck.value = await store.checkByok()
  } finally {
    byokBusy.value = false
  }
}

async function clearByokKey(): Promise<void> {
  byokBusy.value = true
  try {
    if (await store.clearByokKey()) {
      byokForm.api_key = ''
      await loadByokForm()
    }
  } finally {
    byokBusy.value = false
  }
}

async function saveWechat(): Promise<void> {
  wechatBusy.value = true
  try {
    const ok = await store.saveWechat({
      app_id: wechatForm.app_id.trim(),
      app_secret: wechatForm.app_secret.trim() || undefined,
    })
    if (ok) {
      wechatForm.app_secret = ''
      await loadWechatForm()
    }
  } finally {
    wechatBusy.value = false
  }
}

async function checkWechat(): Promise<void> {
  wechatBusy.value = true
  try {
    wechatCheck.value = await store.checkWechat()
  } finally {
    wechatBusy.value = false
  }
}

async function enableWechat(): Promise<void> {
  wechatBusy.value = true
  try {
    if (await store.enableWechat()) await loadWechatForm()
  } finally {
    wechatBusy.value = false
  }
}

async function onSave(name: string) {
  const value = pendingValues[name]?.trim()
  if (!value) return
  saving[name] = true
  try {
    const ok = await store.saveKey(name, value)
    if (ok) pendingValues[name] = ''
  } finally {
    saving[name] = false
  }
}

async function onClear(name: string) {
  saving[name] = true
  try {
    await store.clearKey(name)
  } finally {
    saving[name] = false
  }
}

async function onTogglePublishEnabled(checked: boolean) {
  if (!checked) {
    publishEnabledSaving.value = true
    try { await store.setPublishEnabled(false) } finally { publishEnabledSaving.value = false }
    return
  }
  Modal.confirm({
    title: '确认开启真实发布',
    content: '开启后，allowed_platforms 白名单内的 queued 发布可被真实触发，会真实发出内容且不可撤销。公众号草稿交付也会使用该开关。',
    okText: '确定开启',
    okType: 'danger',
    cancelText: '取消',
    onOk: async () => {
      publishEnabledSaving.value = true
      try { await store.setPublishEnabled(true) } finally { publishEnabledSaving.value = false }
    },
  })
}

async function onSavePlatforms() {
  platformsSaving.value = true
  try { await store.setPublishAllowedPlatforms(pendingPlatforms.value) } finally { platformsSaving.value = false }
}
</script>

<template>
  <h2>设置</h2>
  <a-spin :spinning="loading">
    <a-card title="中转站连接（BYOK）" class="settings-card">
      <a-alert type="info" show-icon class="settings-alert" message="费用数字只用于预算估算，不是中转实付账单。检查连接只读取模型列表，不代表已经能写稿或出图。" />
      <a-alert v-if="!byokMeta.key_set" type="warning" show-icon class="settings-alert" message="尚未保存 API key，写稿和出图前请先填写。" />
      <a-alert v-else-if="!byokMeta.priced" type="warning" show-icon class="settings-alert" message="当前模型未定价。可填写估算单价，否则付费调用会被阻止。" />
      <a-alert v-if="byokMeta.reload_error" type="error" show-icon class="settings-alert" :message="byokMeta.reload_error" />
      <a-form layout="vertical" class="settings-form">
        <a-form-item label="Base URL">
          <a-input v-model:value="byokForm.base_url" placeholder="https://token.example/v1" />
        </a-form-item>
        <a-form-item label="文本模型">
          <a-input v-model:value="byokForm.text_model" placeholder="gpt-5.6-sol" />
        </a-form-item>
        <a-form-item label="图片模型">
          <a-input v-model:value="byokForm.image_model" placeholder="gpt-image-2" />
        </a-form-item>
        <a-form-item label="协议">
          <a-select v-model:value="byokForm.wire_api">
            <a-select-option value="responses">Responses</a-select-option>
            <a-select-option value="chat_completions">Chat Completions</a-select-option>
          </a-select>
        </a-form-item>
        <a-form-item :label="byokMeta.key_set ? `API key（已设置 ${byokMeta.masked}，留空保留）` : 'API key'">
          <a-input-password v-model:value="byokForm.api_key" placeholder="不会回填明文" autocomplete="new-password" />
        </a-form-item>
        <a-form-item label="估算输入单价（USD / 百万 token）">
          <a-input-number v-model:value="byokForm.input_price" :min="0" :step="0.1" class="full-width" placeholder="gpt-5.6-sol 官方参考 4" />
        </a-form-item>
        <a-form-item label="估算输出单价（USD / 百万 token）">
          <a-input-number v-model:value="byokForm.output_price" :min="0" :step="0.1" class="full-width" placeholder="gpt-5.6-sol 官方参考 20" />
        </a-form-item>
      </a-form>
      <div class="settings-actions">
        <a-button type="primary" :loading="byokBusy" @click="saveByok">保存连接</a-button>
        <a-button :loading="byokBusy" @click="checkByok">检查模型列表</a-button>
        <a-button v-if="byokMeta.key_set" danger :loading="byokBusy" @click="clearByokKey">清除 key</a-button>
      </div>
      <p v-if="byokCheck" class="settings-note">{{ byokCheck.ok ? '检查通过：' : '检查未通过：' }}{{ byokCheck.message }}</p>
    </a-card>

    <a-card title="微信公众号（main）" class="settings-card">
      <a-alert type="info" show-icon class="settings-alert" message="只验证凭据和草稿列表读取，不承诺已经验证写入权限。保存位置必须与发送方读取的文件一致。" />
      <a-alert v-if="wechatMeta.warning" type="warning" show-icon class="settings-alert" :message="wechatMeta.warning" />
      <p class="settings-note">凭据文件：{{ wechatMeta.credentials_path || 'secrets/wechat_mp_main.json' }}</p>
      <a-form layout="vertical" class="settings-form">
        <a-form-item label="AppID">
          <a-input v-model:value="wechatForm.app_id" placeholder="wx 开头的 18 位 AppID" />
        </a-form-item>
        <a-form-item :label="wechatMeta.configured ? 'AppSecret（已保存，修改 AppID 时必须重填）' : 'AppSecret'">
          <a-input-password v-model:value="wechatForm.app_secret" placeholder="不会回填明文" autocomplete="new-password" />
        </a-form-item>
      </a-form>
      <div class="settings-actions">
        <a-button type="primary" :loading="wechatBusy" @click="saveWechat">保存公众号</a-button>
        <a-button :loading="wechatBusy" @click="checkWechat">检查连接</a-button>
        <a-button :loading="wechatBusy" :disabled="!wechatMeta.configured" @click="enableWechat">启用草稿交付</a-button>
      </div>
      <p class="settings-note">草稿交付：{{ wechatMeta.delivery_enabled ? '已启用 wechat_mp 白名单' : '尚未启用' }}</p>
      <p v-if="wechatCheck" class="settings-note">{{ wechatCheck.ok ? '检查通过：' : '检查未通过：' }}{{ wechatCheck.message }}</p>
    </a-card>

    <a-card title="通用发布设置（次要）" class="settings-card">
      <a-alert type="warning" show-icon class="settings-alert" message="publish.enabled + allowed_platforms 是真实发布总闸。公众号草稿交付也走这条开关，不要绕过。" />
      <a-space align="center" class="settings-alert">
        <a-switch :checked="publishEnabled" :loading="publishEnabledSaving" @change="onTogglePublishEnabled" />
        <span>{{ publishEnabled ? '已开启真实发布' : '已关闭（仅 dry-run）' }}</span>
      </a-space>
      <h4>平台白名单</h4>
      <a-checkbox-group v-model:value="pendingPlatforms" :options="knownPlatforms" />
      <a-empty v-if="knownPlatforms.length === 0" description="config.yaml 里未配置任何 platforms" />
      <div class="settings-actions">
        <a-button type="primary" size="small" :loading="platformsSaving" @click="onSavePlatforms">保存白名单</a-button>
      </div>
    </a-card>

    <a-card title="其他 API Key" class="settings-card">
      <a-alert v-if="textProvider?.error" type="warning" show-icon class="settings-alert" :message="`文本 provider 未选定：${textProvider.error}`" />
      <a-alert v-else-if="textProvider?.name" type="info" show-icon class="settings-alert" :message="`当前文本 provider：${textProvider.name}（${textProvider.reason}）`" />
      <div v-for="group in keyGroups" :key="group.group" class="key-group">
        <h4>{{ group.label }}</h4>
        <div v-for="item in group.keys" :key="item.name" class="key-row">
          <code>{{ item.name }}</code>
          <a-tag v-if="item.set" color="green">已设置（{{ item.masked }}）</a-tag>
          <a-tag v-else>未设置</a-tag>
          <a-input-password v-model:value="pendingValues[item.name]" placeholder="输入新值以保存/覆盖" />
          <a-button type="primary" size="small" :loading="saving[item.name]" :disabled="!pendingValues[item.name]?.trim()" @click="onSave(item.name)">保存</a-button>
          <a-button v-if="item.set" size="small" danger :loading="saving[item.name]" @click="onClear(item.name)">清除</a-button>
        </div>
      </div>
    </a-card>

    <a-card title="Doctor 体检" class="settings-card">
      <a-list size="small" :data-source="doctor">
        <template #renderItem="{ item }">
          <a-list-item>
            <a-tag :color="item.ok ? 'green' : 'red'">{{ item.ok ? '✓' : '✗' }}</a-tag>
            <strong>{{ item.name }}</strong>
            <span class="muted">{{ item.hint }}</span>
          </a-list-item>
        </template>
      </a-list>
    </a-card>

    <a-card title="Config（脱敏展示）">
      <pre v-if="config" class="config-json">{{ JSON.stringify(config, null, 2) }}</pre>
      <a-empty v-else description="无 config" />
    </a-card>
  </a-spin>
</template>

<style scoped>
.settings-card { margin-bottom: 16px; }
.settings-alert { margin-bottom: 12px; }
.settings-form { max-width: 720px; }
.settings-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
.settings-note { margin: 8px 0 0; color: #666; word-break: break-word; }
.full-width, .settings-form :deep(.ant-input-number), .settings-form :deep(.ant-select) { width: 100%; }
.key-group { margin-bottom: 16px; }
.key-row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 8px; }
.key-row code { min-width: 12em; }
.key-row :deep(.ant-tag), .settings-form :deep(.ant-form-item-label) { max-width: 100%; overflow-wrap: anywhere; white-space: normal; }
.key-row :deep(.ant-input-password) { flex: 1 1 220px; min-width: 0; }
.muted { margin-left: 8px; color: #666; }
.config-json { background: #f5f5f5; padding: 12px; border-radius: 4px; overflow: auto; max-height: 500px; }
@media (max-width: 640px) {
  .key-row code { min-width: 0; width: 100%; }
}
</style>
