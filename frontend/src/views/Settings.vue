<script setup lang="ts">
// M10-8 Settings：脱敏 config + doctor 报告
// API Key 配置改造：新增可写的全局服务 key（LLM/image-gen）表单
// 发布总开关 publish.enabled + allowed_platforms 白名单改为可从本页操作
// （用户明确要求，见 TASKS.md「待评估事项（用户临场提需求，2026-07-16）」），
// 不必手改 config.yaml
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { Modal } from 'ant-design-vue'
import { useSettingsStore } from '../stores'
import { storeToRefs } from 'pinia'

const store = useSettingsStore()
const { config, doctor, keyGroups, loading } = storeToRefs(store)

// 每个 key 名对应的输入框暂存值（不回填已保存的明文，只在提交时读取）
const pendingValues = reactive<Record<string, string>>({})
const saving = reactive<Record<string, boolean>>({})

onMounted(() => {
  store.load()
  store.loadKeys()
})

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

// ── 发布总开关 ────────────────────────────────────────────
const publishEnabled = computed(() => config.value?.publish?.enabled === true)
const knownPlatforms = computed(() => Object.keys(config.value?.platforms ?? {}))
const publishEnabledSaving = ref(false)
const platformsSaving = ref(false)
const pendingPlatforms = ref<string[]>([])

watch(
  config,
  (c) => {
    pendingPlatforms.value = [...((c?.publish?.allowed_platforms as string[]) ?? [])]
  },
  { immediate: true },
)

async function onTogglePublishEnabled(checked: boolean) {
  if (!checked) {
    publishEnabledSaving.value = true
    try {
      await store.setPublishEnabled(false)
    } finally {
      publishEnabledSaving.value = false
    }
    return
  }
  Modal.confirm({
    title: '确认开启真实发布',
    content:
      '开启后，allowed_platforms 白名单内的 queued 发布可被真实触发（含「立即发布」按钮与命令行 publish 命令），会真实发出内容且不可撤销，确定开启吗？',
    okText: '确定开启',
    okType: 'danger',
    cancelText: '取消',
    onOk: async () => {
      publishEnabledSaving.value = true
      try {
        await store.setPublishEnabled(true)
      } finally {
        publishEnabledSaving.value = false
      }
    },
  })
}

async function onSavePlatforms() {
  platformsSaving.value = true
  try {
    await store.setPublishAllowedPlatforms(pendingPlatforms.value)
  } finally {
    platformsSaving.value = false
  }
}
</script>

<template>
  <h2>设置</h2>
  <a-spin :spinning="loading">
    <a-card title="发布总开关" style="margin-bottom: 16px">
      <a-alert
        type="warning"
        show-icon
        style="margin-bottom: 16px"
        message="publish.enabled + allowed_platforms 是真实发布的总闸门；关闭时任何路径（含命令行）都无法真实发布，只能 dry-run。"
      />
      <a-space align="center" style="margin-bottom: 16px">
        <a-switch
          :checked="publishEnabled"
          :loading="publishEnabledSaving"
          @change="onTogglePublishEnabled"
        />
        <span>{{ publishEnabled ? '已开启真实发布' : '已关闭（仅 dry-run）' }}</span>
      </a-space>
      <div>
        <h4>平台白名单（allowed_platforms）</h4>
        <a-checkbox-group v-model:value="pendingPlatforms" :options="knownPlatforms" />
        <a-empty v-if="knownPlatforms.length === 0" description="config.yaml 里未配置任何 platforms" />
        <div style="margin-top: 12px">
          <a-button type="primary" size="small" :loading="platformsSaving" @click="onSavePlatforms">
            保存白名单
          </a-button>
        </div>
      </div>
    </a-card>

    <a-card title="API Key 配置" style="margin-bottom: 16px">
      <div v-for="group in keyGroups" :key="group.group" style="margin-bottom: 16px">
        <h4>{{ group.label }}</h4>
        <a-space v-for="item in group.keys" :key="item.name" style="width: 100%; margin-bottom: 8px" align="baseline">
          <span style="display: inline-block; width: 200px; font-family: monospace">{{ item.name }}</span>
          <a-tag v-if="item.set" color="green">已设置（{{ item.masked }}）</a-tag>
          <a-tag v-else color="default">未设置</a-tag>
          <a-input-password
            v-model:value="pendingValues[item.name]"
            placeholder="输入新值以保存/覆盖"
            style="width: 320px"
          />
          <a-button
            type="primary"
            size="small"
            :loading="saving[item.name]"
            :disabled="!pendingValues[item.name]?.trim()"
            @click="onSave(item.name)"
          >
            保存
          </a-button>
          <a-button
            v-if="item.set"
            size="small"
            danger
            :loading="saving[item.name]"
            @click="onClear(item.name)"
          >
            清除
          </a-button>
        </a-space>
      </div>
      <a-empty v-if="keyGroups.length === 0" description="无 key 分组" />
    </a-card>

    <a-card title="Doctor 体检" style="margin-bottom: 16px">
      <a-list size="small" :data-source="doctor">
        <template #renderItem="{ item }">
          <a-list-item>
            <a-tag :color="item.ok ? 'green' : 'red'">{{ item.ok ? '✓' : '✗' }}</a-tag>
            <strong style="margin-left: 8px">{{ item.name }}</strong>
            <span style="margin-left: 8px; color: #666">{{ item.hint }}</span>
          </a-list-item>
        </template>
        <template #empty>
          <span style="color: #999">无 doctor 报告</span>
        </template>
      </a-list>
    </a-card>

    <a-card title="Config（脱敏展示）">
      <pre v-if="config" style="background: #f5f5f5; padding: 12px; border-radius: 4px; overflow: auto; max-height: 500px">{{ JSON.stringify(config, null, 2) }}</pre>
      <a-empty v-else description="无 config" />
    </a-card>
  </a-spin>
</template>
