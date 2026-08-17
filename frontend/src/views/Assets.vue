<script setup lang="ts">
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import { FileImageOutlined } from '@ant-design/icons-vue'
import { useLibraryStore } from '../stores'

const router = useRouter()
const store = useLibraryStore()
const { items, loading, error } = storeToRefs(store)

onMounted(() => { void store.load() })
</script>

<template>
  <section class="assets-page">
    <header>
      <p class="eyebrow">资产</p>
      <h1>本仓库项目里的视觉资产。</h1>
      <p>这里列出各项目 `vas_` 候选和已选图，不再是路线图占位页。</p>
    </header>
    <a-alert v-if="error" type="error" :message="error" show-icon class="notice" />
    <a-spin :spinning="loading">
      <div v-if="items.length" class="asset-grid">
        <button v-for="item in items" :key="item.id" class="asset-card" type="button" @click="router.push(`/projects/${item.project_id}`)">
          <img v-if="item.url" :src="item.url" :alt="item.prompt" />
          <div v-else class="asset-missing"><FileImageOutlined /></div>
          <div>
            <strong>{{ item.id }}</strong>
            <p>{{ item.project_title }} · {{ item.status === 'selected' ? '已选择' : item.status === 'failed' ? '失败' : '候选' }}</p>
            <small>{{ item.prompt }}</small>
          </div>
        </button>
      </div>
      <a-empty v-else-if="!loading" description="还没有 vas_ 资产。打开一个项目，导入 PNG 或生成候选后会出现在这里。" />
    </a-spin>
  </section>
</template>

<style scoped>
.assets-page { max-width: 1020px; padding: 24px 0 56px; }
.eyebrow { margin: 0 0 8px; color: #7a6650; font-size: 12px; font-weight: 700; letter-spacing: .09em; text-transform: uppercase; }
h1 { margin: 0 0 12px; color: #292522; font-family: Georgia, 'Songti SC', serif; font-size: clamp(30px, 4vw, 44px); line-height: 1.2; }
header p, .asset-card p, .asset-card small { color: #706b65; line-height: 1.65; }
.notice { margin: 16px 0; }
.asset-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }
.asset-card { display: grid; gap: 8px; padding: 10px; text-align: left; border: 1px solid #e8e1d5; border-radius: 10px; background: #fffdf8; cursor: pointer; }
.asset-card img, .asset-missing { width: 100%; aspect-ratio: 16 / 9; object-fit: cover; border-radius: 6px; background: #f3eee6; }
.asset-missing { display: grid; place-items: center; color: #b39b79; font-size: 28px; }
.asset-card strong { color: #292522; }
@media (max-width: 760px) { .asset-grid { grid-template-columns: 1fr; } }
</style>
