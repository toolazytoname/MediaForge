<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { useSettingsStore } from '../stores'

const store = useSettingsStore()
const { keyGroups, openaiImageBaseUrl, wechatMp, loading } = storeToRefs(store)

const wechatAppId = ref('')
const wechatAppSecret = ref('')
const wechatSaving = ref(false)
const wechatProbing = ref(false)
const wechatProbe = ref<{ ok: boolean; message: string } | null>(null)

const openaiKeyInput = ref('')
const openaiSaving = ref(false)
const openaiImageBaseUrlInput = ref('')
const openaiImageBaseUrlSaving = ref(false)

const openaiKey = computed(() =>
  keyGroups.value.flatMap(group => group.keys).find(item => item.name === 'OPENAI_API_KEY') ?? null,
)

onMounted(() => {
  store.load()
  store.loadKeys()
  store.loadOpenAIImageBaseUrl()
  store.loadWechatMp()
})

async function onSaveWechat(): Promise<void> {
  if (!wechatAppId.value.trim() || !wechatAppSecret.value.trim()) return
  wechatSaving.value = true
  wechatProbe.value = null
  try {
    const ok = await store.saveWechatMp(wechatAppId.value.trim(), wechatAppSecret.value.trim())
    if (ok) {
      wechatAppId.value = ''
      wechatAppSecret.value = ''
    }
  } finally {
    wechatSaving.value = false
  }
}

async function onClearWechat(): Promise<void> {
  wechatSaving.value = true
  wechatProbe.value = null
  try {
    await store.clearWechatMp()
  } finally {
    wechatSaving.value = false
  }
}

async function onProbeWechat(): Promise<void> {
  wechatProbing.value = true
  try {
    wechatProbe.value = await store.probeWechatMp()
  } finally {
    wechatProbing.value = false
  }
}

async function onSaveOpenAI(): Promise<void> {
  const value = openaiKeyInput.value.trim()
  if (!value) return
  openaiSaving.value = true
  try {
    const ok = await store.saveKey('OPENAI_API_KEY', value)
    if (ok) openaiKeyInput.value = ''
  } finally {
    openaiSaving.value = false
  }
}

async function onClearOpenAI(): Promise<void> {
  openaiSaving.value = true
  try {
    await store.clearKey('OPENAI_API_KEY')
  } finally {
    openaiSaving.value = false
  }
}

async function onSaveRelay(): Promise<void> {
  const value = openaiImageBaseUrlInput.value.trim()
  if (!value) return
  openaiImageBaseUrlSaving.value = true
  try {
    const ok = await store.saveOpenAIImageBaseUrl(value)
    if (ok) openaiImageBaseUrlInput.value = ''
  } finally {
    openaiImageBaseUrlSaving.value = false
  }
}

async function onClearRelay(): Promise<void> {
  openaiImageBaseUrlSaving.value = true
  try {
    await store.clearOpenAIImageBaseUrl()
  } finally {
    openaiImageBaseUrlSaving.value = false
  }
}
</script>

<template>
  <section class="settings">
    <header>
      <p class="eyebrow">设置</p>
      <h1>只留现在用得上的配置。</h1>
      <p>密钥只存在这台电脑上，页面不会再显示明文。</p>
    </header>

    <a-spin :spinning="loading">
      <article class="panel">
        <div class="panel-head">
          <div>
            <p class="eyebrow">1 · 微信公众号</p>
            <h2>送进草稿箱，不群发。</h2>
          </div>
          <span :class="['status', wechatMp?.configured ? 'on' : 'off']">
            {{ wechatMp?.configured ? `已配置 ${wechatMp.app_id_masked}` : '还没填' }}
          </span>
        </div>
        <p class="help">在公众号后台「设置与开发 → 基本配置」复制 AppID 和 AppSecret。家庭宽带要把当前公网 IP 加进 IP 白名单，否则检查会失败。</p>
        <div class="fields">
          <label>AppID<a-input v-model:value="wechatAppId" placeholder="wx 开头" autocomplete="off" /></label>
          <label>AppSecret<a-input-password v-model:value="wechatAppSecret" placeholder="不会回显已保存的值" autocomplete="new-password" /></label>
        </div>
        <div class="actions">
          <a-button type="primary" :loading="wechatSaving" :disabled="!wechatAppId.trim() || !wechatAppSecret.trim()" @click="onSaveWechat">保存</a-button>
          <a-button :loading="wechatProbing" :disabled="!wechatMp?.configured" @click="onProbeWechat">检查连通</a-button>
          <a-button v-if="wechatMp?.configured" danger :loading="wechatSaving" @click="onClearWechat">清除</a-button>
        </div>
        <p v-if="wechatProbe" :class="['probe', wechatProbe.ok ? 'ok' : 'bad']">{{ wechatProbe.message }}</p>
      </article>

      <article class="panel">
        <div class="panel-head">
          <div>
            <p class="eyebrow">2 · 写作和配图</p>
            <h2>生成文章和插图用的密钥。</h2>
          </div>
          <span :class="['status', openaiKey?.set ? 'on' : 'off']">
            {{ openaiKey?.set ? `已配置 ${openaiKey.masked}` : '还没填' }}
          </span>
        </div>
        <div class="fields">
          <label>API Key<a-input-password v-model:value="openaiKeyInput" placeholder="输入新值以保存或覆盖" autocomplete="new-password" /></label>
          <label>图片中转（可选）<a-input v-model:value="openaiImageBaseUrlInput" placeholder="https://example.com/v1" /></label>
        </div>
        <p class="help">中转必须是 HTTPS，并以 /v1 结尾。当前：{{ openaiImageBaseUrl || '默认官方接口' }}</p>
        <div class="actions">
          <a-button type="primary" :loading="openaiSaving" :disabled="!openaiKeyInput.trim()" @click="onSaveOpenAI">保存密钥</a-button>
          <a-button v-if="openaiKey?.set" danger :loading="openaiSaving" @click="onClearOpenAI">清除密钥</a-button>
          <a-button :loading="openaiImageBaseUrlSaving" :disabled="!openaiImageBaseUrlInput.trim()" @click="onSaveRelay">保存中转</a-button>
          <a-button v-if="openaiImageBaseUrl" danger :loading="openaiImageBaseUrlSaving" @click="onClearRelay">恢复默认</a-button>
        </div>
      </article>

      <article class="panel quiet">
        <div class="panel-head">
          <div>
            <p class="eyebrow">3 · 安全</p>
            <h2>群发是关着的。</h2>
          </div>
          <span class="status off">不会自动发到任何平台</span>
        </div>
        <p class="help">公众号只进草稿箱。头条和小红书的真发开关不在这个页面上，避免误开。</p>
      </article>
    </a-spin>
  </section>
</template>

<style scoped>
.settings { max-width: 720px; padding: 24px 0 64px; }
.eyebrow { margin: 0 0 8px; color: #7a6650; font-size: 12px; font-weight: 700; letter-spacing: .09em; text-transform: uppercase; }
h1, h2 { margin: 0; color: #292522; font-family: Georgia, 'Songti SC', serif; }
h1 { margin-bottom: 10px; font-size: clamp(28px, 4vw, 40px); line-height: 1.2; }
h2 { font-size: 22px; }
header p, .help { color: #706b65; line-height: 1.7; }
.panel { margin-top: 22px; padding: 22px; border: 1px solid #e8e1d5; border-radius: 14px; background: #fffdf8; }
.panel.quiet { background: #f7f3ec; }
.panel-head { display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; margin-bottom: 12px; }
.status { flex: none; padding: 4px 10px; border-radius: 999px; font-size: 12px; }
.status.on { color: #2f6b43; background: #e5f3ea; }
.status.off { color: #7a6650; background: #efe7db; }
.fields { display: grid; gap: 12px; }
.fields label { display: grid; gap: 6px; color: #5c564f; font-size: 13px; font-weight: 600; }
.actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 14px; }
.probe { margin: 14px 0 0; line-height: 1.6; }
.probe.ok { color: #2f6b43; }
.probe.bad { color: #a33b32; }
@media (max-width: 640px) { .panel-head { flex-direction: column; } }
</style>
