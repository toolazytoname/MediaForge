<script setup lang="ts">
// 蚁小二式"添加账号"弹窗：已支持平台网格(点击展开登录引导) + 规划中平台占位分组。
// 红线：只展示引导，不收集账号密码/密钥；唯一"操作"是复制 CLI 命令到剪贴板。
// U7-7: scan_qr 平台新增「🚀 一键登录」按钮，调用 store.loginAccount
//       实时显示进度（来自 R7-7 log_event 链路）；CLI 命令仍在 <details> 折叠区兜底
import { computed, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import PlatformBadge from './PlatformBadge.vue'
import { SUPPORTED_PLATFORMS, PLANNED_PLATFORMS, platformMeta } from './platformMeta'
import { storeToRefs } from 'pinia'
import { useAccountsStore, unwrapError, type LoginRunState } from '../../stores'

interface Props {
  open: boolean
  items: AccountHealthItem[]
  guidance: LoginGuidance[]
  preselect?: string | null
}

const props = withDefaults(defineProps<Props>(), { preselect: null })
const emit = defineEmits<{
  (e: 'update:open', value: boolean): void
}>()

const selected = ref<string | null>(null)
const currentRunId = ref<string | null>(null)
const oneClickError = ref<string | null>(null)

const store = useAccountsStore()
const { runningLogins } = storeToRefs(store)

watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) {
      selected.value = props.preselect ?? null
      currentRunId.value = null
      oneClickError.value = null
    } else {
      // 关闭弹窗时清掉当前 run id（保留 runningLogins 状态以让 toast 正常出现）
      currentRunId.value = null
    }
  },
)

const healthByPlatform = computed<Map<string, { total: number; healthy: number }>>(() => {
  const m = new Map<string, { total: number; healthy: number }>()
  for (const it of props.items) {
    const cur = m.get(it.platform) ?? { total: 0, healthy: 0 }
    m.set(it.platform, {
      total: cur.total + 1,
      healthy: cur.healthy + (it.healthy ? 1 : 0),
    })
  }
  return m
})

const guidanceByPlatform = computed<Map<string, LoginGuidance>>(() => {
  const m = new Map<string, LoginGuidance>()
  for (const g of props.guidance) m.set(g.platform, g)
  return m
})

const selectedGuidance = computed<LoginGuidance | null>(() =>
  selected.value ? guidanceByPlatform.value.get(selected.value) ?? null : null,
)

// 拿当前 run 的最新状态
const currentLoginState = computed<LoginRunState | null>(() => {
  if (currentRunId.value === null) return null
  return runningLogins.value.get(currentRunId.value) ?? null
})

const isLoginInProgress = computed<boolean>(() => {
  const s = currentLoginState.value
  if (!s) return false
  return s.status === 'queued' || s.status === 'running'
})

const loginProgressText = computed<string>(() => {
  const s = currentLoginState.value
  if (!s) return ''
  if (s.status === 'queued') return s.message || '排队中...'
  if (s.status === 'running') return s.message || '登录中...'
  if (s.status === 'succeeded') return '✓ 登录完成'
  if (s.status === 'failed') {
    return s.error_message || s.message || '登录失败'
  }
  return ''
})

const loginStatusTag = computed<{ color: string; text: string }>(() => {
  const s = currentLoginState.value
  if (!s) return { color: 'default', text: '' }
  switch (s.status) {
    case 'queued':
      return { color: 'blue', text: '排队中' }
    case 'running':
      return { color: 'processing', text: '进行中' }
    case 'succeeded':
      return { color: 'green', text: '成功' }
    case 'failed':
      return { color: 'red', text: '失败' }
    default:
      return { color: 'default', text: s.status }
  }
})

function healthLabel(key: string): string {
  const h = healthByPlatform.value.get(key)
  if (!h || h.total === 0) return '未授权'
  return `${h.healthy}/${h.total} 健康`
}

function close(value: boolean): void {
  emit('update:open', value)
}

function selectPlatform(key: string): void {
  selected.value = key
  currentRunId.value = null
  oneClickError.value = null
}

async function copyCommand(cmd: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(cmd)
    message.success('已复制到剪贴板')
  } catch {
    message.warning('复制失败，请手动选中命令文本')
  }
}

async function onOneClickLogin(): Promise<void> {
  if (selected.value === null) return
  oneClickError.value = null
  try {
    const runId = await store.loginAccount(selected.value, 'main')
    currentRunId.value = runId
    message.success(`登录已启动：${platformMeta(selected.value).label} / main`)
  } catch (e) {
    const errMsg = unwrapError(e)
    oneClickError.value = errMsg
    message.error(`启动登录失败: ${errMsg}`)
  }
}
</script>

<template>
  <a-modal
    :open="open"
    title="添加账号"
    :footer="null"
    width="720px"
    @update:open="close"
  >
    <h4 class="section-title">已支持平台</h4>
    <div class="platform-grid">
      <div
        v-for="p in SUPPORTED_PLATFORMS"
        :key="p.key"
        class="platform-tile"
        :class="{ 'is-selected': selected === p.key }"
        @click="selectPlatform(p.key)"
      >
        <PlatformBadge :platform="p.key" />
        <span class="tile-name">{{ platformMeta(p.key).label }}</span>
        <span class="tile-health">{{ healthLabel(p.key) }}</span>
      </div>
    </div>

    <div v-if="selectedGuidance" class="guidance-panel">
      <div class="guidance-header">
        <PlatformBadge :platform="selectedGuidance.platform" size="small" />
        <span class="guidance-platform-name">{{ platformMeta(selectedGuidance.platform).label }}</span>
        <a-tag :color="selectedGuidance.auth_type === 'scan_qr' ? 'purple' : 'blue'">
          {{ selectedGuidance.auth_type === 'scan_qr' ? '扫码登录' : '配置凭据' }}
        </a-tag>
      </div>
      <template v-if="selectedGuidance.auth_type === 'scan_qr'">
        <p class="guidance-hint">点击下方按钮，桌面会弹出浏览器完成扫码登录；进度实时显示在此处。</p>
        <div class="one-click-row">
          <a-button
            type="primary"
            :loading="isLoginInProgress"
            :disabled="isLoginInProgress"
            @click="onOneClickLogin"
          >
            🚀 一键登录
          </a-button>
          <a-tag v-if="currentLoginState" :color="loginStatusTag.color">
            {{ loginStatusTag.text }}
          </a-tag>
          <span v-if="loginProgressText" class="login-progress">{{ loginProgressText }}</span>
        </div>
        <a-alert
          v-if="oneClickError"
          type="error"
          show-icon
          :message="`启动失败: ${oneClickError}`"
          style="margin-bottom: 8px"
        />
        <details class="cli-fallback">
          <summary>终端命令兜底（远程服务器 / 失败重试用）</summary>
          <div class="cmd-row">
            <code class="cmd-text">{{ selectedGuidance.command }}</code>
            <a-button size="small" @click="copyCommand(selectedGuidance.command)">复制</a-button>
          </div>
          <p class="guidance-notes">{{ selectedGuidance.notes }}</p>
        </details>
      </template>
      <template v-else>
        <p class="guidance-notes">{{ selectedGuidance.notes }}</p>
        <div v-if="selectedGuidance.command" class="cmd-row">
          <code class="cmd-text">{{ selectedGuidance.command }}</code>
        </div>
      </template>
      <p class="guidance-footnote">授权完成后回到本页刷新即可看到健康状态（登录成功会自动刷新）。</p>
    </div>

    <h4 class="section-title section-title-planned">规划中（暂不支持）</h4>
    <div class="platform-grid">
      <div
        v-for="p in PLANNED_PLATFORMS"
        :key="p.key"
        class="platform-tile is-planned"
        @click="message.info('暂未支持，如需要请在 docs/TASKS.md 提需求')"
      >
        <PlatformBadge :platform="p.key" />
        <span class="tile-name">{{ platformMeta(p.key).label }}</span>
        <a-tag color="default" size="small">规划中</a-tag>
      </div>
    </div>
  </a-modal>
</template>

<style scoped>
.section-title {
  margin: 0 0 12px;
  font-size: 13px;
  font-weight: 600;
  color: #595959;
}
.section-title-planned {
  margin-top: 20px;
}
.platform-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
  gap: 10px;
}
.platform-tile {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 12px 8px;
  border: 1px solid #f0f0f0;
  border-radius: 8px;
  cursor: pointer;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.platform-tile:hover {
  border-color: #7c4dff;
  box-shadow: 0 2px 8px rgba(124, 77, 255, 0.1);
}
.platform-tile.is-selected {
  border-color: #7c4dff;
  background: #f7f4ff;
}
.platform-tile.is-planned {
  opacity: 0.55;
  cursor: not-allowed;
}
.platform-tile.is-planned:hover {
  border-color: #f0f0f0;
  box-shadow: none;
}
.tile-name {
  font-size: 12px;
  font-weight: 500;
  color: #262626;
}
.tile-health {
  font-size: 11px;
  color: #8c8c8c;
}
.guidance-panel {
  margin-top: 16px;
  padding: 12px 14px;
  border: 1px dashed #d9d9d9;
  border-radius: 8px;
  background: #fafafa;
}
.guidance-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.guidance-platform-name {
  font-size: 13px;
  font-weight: 600;
  color: #1f1f1f;
}
.guidance-hint {
  margin: 0 0 8px;
  font-size: 12px;
  color: #595959;
}
.one-click-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 10px;
  padding: 8px 10px;
  background: #fff;
  border-radius: 6px;
  border: 1px solid #e8e8e8;
}
.login-progress {
  font-size: 12px;
  color: #595959;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  flex: 1;
  word-break: break-all;
}
.cli-fallback {
  margin-top: 8px;
  padding: 8px 10px;
  background: #fafafa;
  border: 1px solid #f0f0f0;
  border-radius: 6px;
}
.cli-fallback summary {
  cursor: pointer;
  font-size: 12px;
  color: #595959;
  user-select: none;
}
.cli-fallback[open] summary {
  margin-bottom: 8px;
}
.cmd-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.cmd-text {
  flex: 1;
  padding: 6px 10px;
  background: #1f1f1f;
  color: #d9d9d9;
  border-radius: 6px;
  font-size: 12px;
  word-break: break-all;
}
.guidance-notes {
  margin: 0;
  font-size: 12px;
  color: #595959;
  line-height: 1.6;
}
.guidance-footnote {
  margin: 8px 0 0;
  font-size: 11px;
  color: #8c8c8c;
}
