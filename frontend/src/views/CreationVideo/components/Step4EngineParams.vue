<script setup lang="ts">
// Step 4：引擎参数——时长/宽高比通用；数字人口播额外需要形象模板
import { computed, onMounted } from 'vue'
import { storeToRefs } from 'pinia'
import { useSettingsStore, useVideoCreationStore, type VideoAspect } from '../../../stores'

const props = defineProps<{
  engine: 'mpt' | 'pixelle' | 'digitalhuman' | null
  durationS: number
}>()

const emit = defineEmits<{
  (e: 'update:durationS', v: number): void
}>()

const store = useVideoCreationStore()
const { aspect, style } = storeToRefs(store)
const settingsStore = useSettingsStore()
const { config } = storeToRefs(settingsStore)

onMounted(() => {
  if (!config.value) settingsStore.load().catch(() => null)
})

const avatarTemplateOptions = computed(() => {
  const templates: Record<string, string> =
    config.value?.video?.digitalhuman?.avatar_templates ?? {}
  return Object.keys(templates).map((name) => ({ value: name, label: name }))
})

function onDurationChange(v: number | null) {
  emit('update:durationS', v ?? props.durationS)
}

function onAspectChange(v: VideoAspect) {
  aspect.value = v
}

function onTtsVoiceChange(v: string) {
  style.value = { ...style.value, tts_voice: v }
}

function onAvatarTemplateChange(v: string) {
  style.value = { ...style.value, avatar_template: v }
}
</script>

<template>
  <div>
    <a-empty v-if="!engine" description="请先完成 Step 2 选择创作类型" />
    <a-form v-else layout="vertical">
      <a-form-item label="目标时长（秒）">
        <a-input-number
          :value="durationS"
          :min="5"
          :max="600"
          style="width: 200px"
          @change="onDurationChange"
        />
      </a-form-item>
      <a-form-item label="画幅比例">
        <a-radio-group :value="aspect" @change="(e: any) => onAspectChange(e.target.value)">
          <a-radio-button value="9:16">9:16 竖屏</a-radio-button>
          <a-radio-button value="16:9">16:9 横屏</a-radio-button>
        </a-radio-group>
      </a-form-item>

      <template v-if="engine === 'digitalhuman'">
        <a-form-item label="TTS 音色">
          <a-input
            :value="style.tts_voice ?? ''"
            placeholder="留空使用 config 默认音色"
            style="width: 280px"
            @change="(e: any) => onTtsVoiceChange(e.target.value)"
          />
        </a-form-item>
        <a-form-item label="数字人形象模板">
          <a-select
            :value="style.avatar_template ?? undefined"
            :options="avatarTemplateOptions"
            placeholder="选择已配置的形象模板"
            allow-clear
            style="width: 280px"
            @change="onAvatarTemplateChange"
          />
          <a-alert
            v-if="avatarTemplateOptions.length === 0"
            type="warning"
            message="config.video.digitalhuman.avatar_templates 未配置任何形象模板"
            show-icon
            style="margin-top: 8px"
          />
        </a-form-item>
      </template>
    </a-form>
  </div>
</template>
