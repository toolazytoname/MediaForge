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
    <figcaption>{{ caption || '应用内阅读预览，不是平台回执' }}</figcaption>
  </figure>
</template>

<style scoped>
.wechat-preview {
  margin: 0;
}

.phone {
  overflow: hidden;
  max-width: 400px;
  min-height: 560px;
  border: 1px solid var(--line-strong);
  border-radius: 20px;
  background: var(--surface);
}

.status {
  display: flex;
  justify-content: space-between;
  padding: 12px 16px 10px;
  color: var(--faint);
  font-size: 11px;
  border-bottom: 1px solid var(--line);
}

.article {
  max-height: 680px;
  overflow: auto;
  padding: 20px 18px 36px;
  color: var(--ink);
}

.article :deep(h1) {
  margin: 0 0 12px;
  font-family: var(--font-read);
  font-size: 22px;
  line-height: 1.35;
  font-weight: 650;
}

.article :deep(h2) {
  margin: 1.6em 0 .6em;
  font-size: 17px;
}

.article :deep(p),
.article :deep(blockquote),
.article :deep(li) {
  font-size: 15px;
  line-height: 1.85;
}

.article :deep(blockquote) {
  margin: 0 0 16px;
  padding-left: 12px;
  border-left: 2px solid var(--line-strong);
  color: var(--muted);
}

.article :deep(img) {
  display: block;
  width: 100%;
  height: auto;
  margin: 16px 0;
}

figcaption {
  margin-top: 10px;
  color: var(--faint);
  font-size: 12px;
}
</style>
