<script setup lang="ts">
// M11-C 账号中心网格化：从 cookie 健康表升级为蚁小二式平台网格授权中心
// - 一平台一卡片:平台名 / 账号总数 / 健康 / 失效数 / 最后校验时间
// - 按平台分组(国内 / 国际)——分组纯 UI 决定,数据走 /accounts + /accounts/login-guidance 不变
// - 灰色 = 无账号 (引导登录); 绿色 = 全部健康; 黄色 = 有失效
// - 真实授权走 CLI `login`,UI 仅展示与引导
import { computed, onMounted } from 'vue'
import { useAccountsStore } from '../stores'
import { storeToRefs } from 'pinia'
import type { AccountHealthItem, LoginGuidance } from '../stores'

const store = useAccountsStore()
const { items, guidance, loading, loaded } = storeToRefs(store)

interface PlatformTile {
  name: string
  group: 'domestic' | 'intl'
  accounts: AccountHealthItem[]
  healthyCount: number
  totalCount: number
  latestCheckAt: string | null
}

const ALL_KNOWN_PLATFORMS: readonly { name: string; group: 'domestic' | 'intl' }[] = [
  { name: 'toutiao', group: 'domestic' },
  { name: 'xiaohongshu', group: 'domestic' },
  { name: 'douyin', group: 'domestic' },
  { name: 'x', group: 'intl' },
]

// 按平台聚合账号,合并 ALL_KNOWN_PLATFORMS (空平台也要渲染引导)
const platformTiles = computed<PlatformTile[]>(() => {
  const byName = new Map<string, AccountHealthItem[]>()
  for (const it of items.value) {
    const list = byName.get(it.platform) ?? []
    list.push(it)
    byName.set(it.platform, list)
  }
  const tiles: PlatformTile[] = []
  for (const { name, group } of ALL_KNOWN_PLATFORMS) {
    const accs = byName.get(name) ?? []
    const healthy = accs.filter((a) => a.healthy).length
    const latest = accs
      .map((a) => a.last_check_at)
      .filter((s): s is string => Boolean(s))
      .sort()
      .reverse()[0] ?? null
    tiles.push({
      name,
      group,
      accounts: accs,
      healthyCount: healthy,
      totalCount: accs.length,
      latestCheckAt: latest,
    })
  }
  // 自动补全 /accounts 里出现但 ALL_KNOWN_PLATFORMS 没列的（如未来新加平台）
  const known = new Set(ALL_KNOWN_PLATFORMS.map((p) => p.name))
  for (const [name, accs] of byName) {
    if (known.has(name)) continue
    const healthy = accs.filter((a) => a.healthy).length
    const latest = accs
      .map((a) => a.last_check_at)
      .filter((s): s is string => Boolean(s))
      .sort()
      .reverse()[0] ?? null
    tiles.push({
      name, group: 'domestic', accounts: accs,
      healthyCount: healthy, totalCount: accs.length, latestCheckAt: latest,
    })
  }
  return tiles
})

const domesticTiles = computed(() =>
  platformTiles.value.filter((t) => t.group === 'domestic'),
)
const intlTiles = computed(() =>
  platformTiles.value.filter((t) => t.group === 'intl'),
)

const guidanceByName = computed<Map<string, LoginGuidance>>(() => {
  const m = new Map<string, LoginGuidance>()
  for (const g of guidance.value) m.set(g.platform, g)
  return m
})

function guidanceFor(name: string): LoginGuidance | null {
  return guidanceByName.value.get(name) ?? null
}

function tileStateClass(tile: PlatformTile): string {
  if (tile.totalCount === 0) return 'is-empty'
  if (tile.healthyCount === tile.totalCount) return 'is-healthy'
  if (tile.healthyCount === 0) return 'is-failed'
  return 'is-mixed'
}

function formatLastCheck(s: string | null): string {
  if (!s) return '从未'
  return s.replace('T', ' ').replace(/\..+$/, '').slice(0, 19)
}

const allLoaded = computed(() => loaded.value || !loading.value)

onMounted(async () => {
  if (!loaded.value) await store.load()
})
</script>

<template>
  <div>
    <h2>账号管理</h2>
    <a-alert
      type="info"
      show-icon
      style="margin-bottom: 16px"
      message="账号授权通过 CLI 完成（UI 不直接写登录）。"
      description="首次添加：在 shell 跑 `python -m pipeline.run login <platform> <account_id>` 按指引扫码/粘贴 token；后续 cookie 健康由后台 cron 主动 check 并写入此网格。"
    />

    <a-spin :spinning="loading && !allLoaded">
      <!-- 国内平台 -->
      <h3 style="margin: 8px 0 12px">国内平台</h3>
      <a-row :gutter="[12, 12]" style="margin-bottom: 16px">
        <a-col v-for="tile in domesticTiles" :key="tile.name" :xs="24" :sm="12" :md="8" :lg="6">
          <a-card :class="['platform-tile', tileStateClass(tile)]" size="small">
            <template #title>
              <div class="tile-title">
                <span class="tile-platform-name">{{ tile.name }}</span>
                <a-tag v-if="tile.totalCount === 0" color="default">未授权</a-tag>
                <a-tag v-else-if="tile.healthyCount === tile.totalCount" color="green">健康</a-tag>
                <a-tag v-else-if="tile.healthyCount === 0" color="red">失效</a-tag>
                <a-tag v-else color="orange">部分失效</a-tag>
              </div>
            </template>
            <div class="tile-stats">
              <div class="stat-row">
                <span class="stat-label">账号数</span>
                <span class="stat-value">{{ tile.totalCount }}</span>
              </div>
              <div class="stat-row">
                <span class="stat-label">健康</span>
                <span class="stat-value">{{ tile.healthyCount }} / {{ tile.totalCount }}</span>
              </div>
              <div class="stat-row">
                <span class="stat-label">最后校验</span>
                <span class="stat-value-mono">{{ formatLastCheck(tile.latestCheckAt) }}</span>
              </div>
            </div>
            <div v-if="tile.totalCount > 0" class="tile-accounts">
              <div v-for="a in tile.accounts" :key="a.account" class="account-row">
                <span class="account-name">{{ a.account }}</span>
                <a-tag :color="a.healthy ? 'green' : 'red'" size="small">
                  {{ a.healthy ? '✓' : '✗' }}
                </a-tag>
              </div>
            </div>
            <div v-if="guidanceFor(tile.name)" class="tile-guidance">
              <code class="guidance-cmd">{{ guidanceFor(tile.name)?.command }}</code>
              <p class="guidance-notes">{{ guidanceFor(tile.name)?.notes }}</p>
            </div>
          </a-card>
        </a-col>
      </a-row>

      <!-- 国际平台 -->
      <h3 style="margin: 16px 0 12px">国际平台</h3>
      <a-row :gutter="[12, 12]">
        <a-col v-for="tile in intlTiles" :key="tile.name" :xs="24" :sm="12" :md="8" :lg="6">
          <a-card :class="['platform-tile', tileStateClass(tile)]" size="small">
            <template #title>
              <div class="tile-title">
                <span class="tile-platform-name">{{ tile.name }}</span>
                <a-tag v-if="tile.totalCount === 0" color="default">未授权</a-tag>
                <a-tag v-else-if="tile.healthyCount === tile.totalCount" color="green">健康</a-tag>
                <a-tag v-else-if="tile.healthyCount === 0" color="red">失效</a-tag>
                <a-tag v-else color="orange">部分失效</a-tag>
              </div>
            </template>
            <div class="tile-stats">
              <div class="stat-row">
                <span class="stat-label">账号数</span>
                <span class="stat-value">{{ tile.totalCount }}</span>
              </div>
              <div class="stat-row">
                <span class="stat-label">健康</span>
                <span class="stat-value">{{ tile.healthyCount }} / {{ tile.totalCount }}</span>
              </div>
              <div class="stat-row">
                <span class="stat-label">最后校验</span>
                <span class="stat-value-mono">{{ formatLastCheck(tile.latestCheckAt) }}</span>
              </div>
            </div>
            <div v-if="tile.totalCount > 0" class="tile-accounts">
              <div v-for="a in tile.accounts" :key="a.account" class="account-row">
                <span class="account-name">{{ a.account }}</span>
                <a-tag :color="a.healthy ? 'green' : 'red'" size="small">
                  {{ a.healthy ? '✓' : '✗' }}
                </a-tag>
              </div>
            </div>
            <div v-if="guidanceFor(tile.name)" class="tile-guidance">
              <code class="guidance-cmd">{{ guidanceFor(tile.name)?.command }}</code>
              <p class="guidance-notes">{{ guidanceFor(tile.name)?.notes }}</p>
            </div>
          </a-card>
        </a-col>
      </a-row>

      <a-empty
        v-if="domesticTiles.length === 0 && intlTiles.length === 0 && !loading"
        description="未配置任何平台"
        style="margin-top: 32px"
      />
    </a-spin>
  </div>
</template>

<style scoped>
.platform-tile {
  height: 100%;
}
.tile-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.tile-platform-name {
  font-weight: 600;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
.tile-stats {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-bottom: 12px;
  font-size: 12px;
}
.stat-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.stat-label {
  color: #8c8c8c;
}
.stat-value {
  font-weight: 500;
  color: #1f1f1f;
}
.stat-value-mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  color: #595959;
  font-size: 11px;
}
.tile-accounts {
  border-top: 1px solid #f0f0f0;
  padding-top: 8px;
  margin-bottom: 8px;
  max-height: 120px;
  overflow-y: auto;
}
.account-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 4px 0;
  font-size: 12px;
}
.account-name {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  color: #262626;
  word-break: break-all;
}
.tile-guidance {
  border-top: 1px dashed #f0f0f0;
  padding-top: 8px;
  margin-top: 4px;
}
.guidance-cmd {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11px;
  color: #7c4dff;
  word-break: break-all;
  display: block;
}
.guidance-notes {
  margin: 4px 0 0;
  font-size: 11px;
  color: #8c8c8c;
  line-height: 1.4;
}

.platform-tile :deep(.ant-card-head-title) {
  width: 100%;
}

.platform-tile.is-empty :deep(.ant-card-head) {
  background: #fafafa;
}
.platform-tile.is-healthy :deep(.ant-card-head) {
  background: #f6ffed;
}
.platform-tile.is-failed :deep(.ant-card-head) {
  background: #fff1f0;
}
.platform-tile.is-mixed :deep(.ant-card-head) {
  background: #fffbe6;
}
</style>
