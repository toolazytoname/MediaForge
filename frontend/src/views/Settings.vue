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
      <h1>设置</h1>
      <p>只留现在用得上的配置。密钥只存在这台电脑上，页面不会再显示明文。</p>
    </header>

    <a-spin :spinning="loading">
      <article class="panel">
        <div class="panel-head">
          <div>
            <h2>微信公众号</h2>
            <p>送进草稿箱，不群发。</p>
          </div>
          <span :class="['status', wechatMp?.configured ? 'on' : 'off']">
            {{ wechatMp?.configured ? `已配置 ${wechatMp.app_id_masked}` : '还没填' }}
          </span>
        </div>
        <p class="help">在公众号后台「设置与开发 → 基本配置」复制 AppID 和 AppSecret。家庭宽带要把当前公网 IP 加进 IP 白名单，否则检查会失败。</p>
        <div class="fields">
          <label>AppID<input v-model="wechatAppId" type="text" placeholder="wx 开头" autocomplete="off" /></label>
          <label>AppSecret<input v-model="wechatAppSecret" type="password" placeholder="不会回显已保存的值" autocomplete="new-password" /></label>
        </div>
        <div class="actions">
          <button type="button" class="primary" :disabled="wechatSaving || !wechatAppId.trim() || !wechatAppSecret.trim()" @click="onSaveWechat">保存</button>
          <button type="button" class="ghost" :disabled="wechatProbing || !wechatMp?.configured" @click="onProbeWechat">{{ wechatProbing ? '检查中…' : '检查连通' }}</button>
          <button v-if="wechatMp?.configured" type="button" class="danger" :disabled="wechatSaving" @click="onClearWechat">清除</button>
        </div>
        <p v-if="wechatProbe" :class="['probe', wechatProbe.ok ? 'ok' : 'bad']">{{ wechatProbe.message }}</p>
      </article>

      <article class="panel">
        <div class="panel-head">
          <div>
            <h2>写作和配图</h2>
            <p>生成文章和插图用的密钥。</p>
          </div>
          <span :class="['status', openaiKey?.set ? 'on' : 'off']">
            {{ openaiKey?.set ? `已配置 ${openaiKey.masked}` : '还没填' }}
          </span>
        </div>
        <div class="fields">
          <label>API Key<input v-model="openaiKeyInput" type="password" placeholder="输入新值以保存或覆盖" autocomplete="new-password" /></label>
          <label>图片中转（可选）<input v-model="openaiImageBaseUrlInput" type="url" placeholder="https://example.com/v1" /></label>
        </div>
        <p class="help">中转必须是 HTTPS，并以 /v1 结尾。当前：{{ openaiImageBaseUrl || '默认官方接口' }}</p>
        <div class="actions">
          <button type="button" class="primary" :disabled="openaiSaving || !openaiKeyInput.trim()" @click="onSaveOpenAI">保存密钥</button>
          <button v-if="openaiKey?.set" type="button" class="danger" :disabled="openaiSaving" @click="onClearOpenAI">清除密钥</button>
          <button type="button" class="ghost" :disabled="openaiImageBaseUrlSaving || !openaiImageBaseUrlInput.trim()" @click="onSaveRelay">保存中转</button>
          <button v-if="openaiImageBaseUrl" type="button" class="danger" :disabled="openaiImageBaseUrlSaving" @click="onClearRelay">恢复默认</button>
        </div>
      </article>

      <article class="panel quiet">
        <div class="panel-head">
          <div>
            <h2>安全</h2>
            <p>群发是关着的。公众号只进草稿箱。头条和小红书的真发开关不在这个页面上。</p>
          </div>
          <span class="status off">不会自动发到任何平台</span>
        </div>
      </article>
    </a-spin>
  </section>
</template>

<style scoped>
.settings {
  max-width: 680px;
  padding-top: 12px;
}

header h1,
h2 {
  margin: 0;
  font-weight: 560;
  letter-spacing: -0.03em;
}

header h1 {
  margin-bottom: 8px;
  font-size: clamp(28px, 4vw, 40px);
  line-height: 1.15;
}

h2 {
  font-size: 18px;
}

header p,
.help,
.panel-head p {
  margin: 6px 0 0;
  color: var(--muted);
  line-height: 1.65;
}

.panel {
  margin-top: 28px;
  padding: 22px 0 4px;
  border-top: 1px solid var(--line);
}

.panel-head {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: flex-start;
  margin-bottom: 14px;
}

.status {
  flex: none;
  padding: 3px 8px;
  border-radius: 4px;
  font-size: 12px;
}

.status.on {
  color: var(--ok);
  background: var(--ok-wash);
}

.status.off {
  color: var(--muted);
  background: var(--wash);
}

.fields {
  display: grid;
  gap: 14px;
}

.fields label {
  display: grid;
  gap: 6px;
  color: var(--ink);
  font-size: 13px;
  font-weight: 560;
}

.fields input {
  width: 100%;
  height: 40px;
  padding: 0 12px;
  border: 1px solid var(--line-strong);
  border-radius: 6px;
  background: var(--surface);
  color: var(--ink);
}

.fields input:focus {
  outline: none;
  border-color: var(--ink);
}

.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 16px;
}

.primary,
.ghost,
.danger {
  height: 36px;
  padding: 0 14px;
  border-radius: 6px;
  cursor: pointer;
  transition: background-color 200ms var(--ease), transform 200ms var(--ease), opacity 200ms var(--ease);
}

.primary:active,
.ghost:active,
.danger:active {
  transform: scale(0.98);
}

.primary {
  border: 0;
  background: var(--ink);
  color: var(--surface);
}

.ghost {
  border: 1px solid var(--line-strong);
  background: transparent;
  color: var(--ink);
}

.danger {
  border: 1px solid #e4c4c1;
  background: transparent;
  color: var(--bad);
}

.primary:disabled,
.ghost:disabled,
.danger:disabled {
  cursor: not-allowed;
  opacity: 0.4;
}

.probe {
  margin: 14px 0 0;
  line-height: 1.6;
}

.probe.ok {
  color: var(--ok);
}

.probe.bad {
  color: var(--bad);
}

@media (max-width: 640px) {
  .panel-head {
    flex-direction: column;
  }
}
</style>
