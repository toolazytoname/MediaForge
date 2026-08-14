<script setup lang="ts">
defineProps<{
  html: string
  caption?: string
}>()
</script>

<template>
  <figure class="wechat-preview">
    <div class="phone" aria-label="微信公众号阅读预览">
      <div class="status">
        <span>10:24</span>
        <span>微信</span>
      </div>
      <div class="article" v-html="html" />
    </div>
    <figcaption>{{ caption || '应用内阅读预览 · 不是平台回执' }}</figcaption>
  </figure>
</template>

<style scoped>
.wechat-preview { margin: 0; }
.phone {
  overflow: hidden;
  max-width: 420px;
  min-height: 560px;
  border: 1px solid #d8cfc3;
  border-radius: 22px;
  background: #fff;
  box-shadow: 0 18px 40px rgba(70, 52, 28, .08);
}
.status {
  display: flex;
  justify-content: space-between;
  padding: 10px 16px 8px;
  color: #8a8178;
  font-size: 11px;
  border-bottom: 1px solid #f0ebe3;
}
.article {
  max-height: 680px;
  overflow: auto;
  padding: 18px 18px 36px;
  color: #2f2b28;
}
.article :deep(h1) {
  margin: 0 0 12px;
  color: #1f1c1a;
  font-size: 22px;
  line-height: 1.35;
  font-weight: 700;
}
.article :deep(h2) {
  margin: 1.6em 0 .6em;
  font-size: 17px;
}
.article :deep(p), .article :deep(blockquote), .article :deep(li) {
  font-size: 15px;
  line-height: 1.85;
}
.article :deep(blockquote) {
  margin: 0 0 16px;
  padding-left: 12px;
  border-left: 3px solid #e4d8c6;
  color: #6f675f;
}
.article :deep(img) {
  display: block;
  width: 100%;
  height: auto;
  margin: 16px 0;
  border-radius: 4px;
}
figcaption {
  margin-top: 10px;
  color: #948d84;
  font-size: 12px;
}
</style>
