<script setup lang="ts">
// 蚁小二式"添加账号"弹窗：已支持平台网格(点击展开登录引导) + 规划中平台占位分组。
// 红线：只展示引导，不收集账号密码/密钥；唯一"操作"是复制 CLI 命令到剪贴板。
import { computed, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import PlatformBadge from './PlatformBadge.vue'
import { SUPPORTED_PLATFORMS, PLANNED_PLATFORMS } from './platformMeta'
import type { AccountHealthItem, LoginGuidance } from '../../stores'

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

watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) selected.value = props.preselect ?? null
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
}

async function copyCommand(cmd: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(cmd)
    message.success('已复制到剪贴板')
  } catch {
    message.warning('复制失败，请手动选中命令文本')
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
        <span class="tile-health">{{ healthLabel(p.key) }}</span>
      </div>
    </div>

    <div v-if="selectedGuidance" class="guidance-panel">
      <div class="guidance-header">
        <PlatformBadge :platform="selectedGuidance.platform" size="small" />
        <a-tag :color="selectedGuidance.auth_type === 'scan_qr' ? 'purple' : 'blue'">
          {{ selectedGuidance.auth_type === 'scan_qr' ? '扫码登录' : '配置凭据' }}
        </a-tag>
      </div>
      <template v-if="selectedGuidance.auth_type === 'scan_qr'">
        <p class="guidance-hint">在终端里运行以下命令，按提示扫码完成登录：</p>
        <div class="cmd-row">
          <code class="cmd-text">{{ selectedGuidance.command }}</code>
          <a-button size="small" @click="copyCommand(selectedGuidance.command)">复制</a-button>
        </div>
        <p class="guidance-notes">{{ selectedGuidance.notes }}</p>
      </template>
      <template v-else>
        <p class="guidance-notes">{{ selectedGuidance.notes }}</p>
        <div v-if="selectedGuidance.command" class="cmd-row">
          <code class="cmd-text">{{ selectedGuidance.command }}</code>
        </div>
      </template>
      <p class="guidance-footnote">授权在 CLI / 配置文件完成，本页仅展示引导；完成后回到本页刷新即可看到健康状态。</p>
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
.guidance-hint {
  margin: 0 0 8px;
  font-size: 12px;
  color: #595959;
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
</style>
