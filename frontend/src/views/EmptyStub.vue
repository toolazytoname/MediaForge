<script setup lang="ts">
// M10-7 占位 view；M10-8 接入真实数据
defineProps<{ msg?: string }>()
</script>

<template>
  <h2>{{ msg || 'View' }}</h2>
  <p>占位 — M10-8 接入真实数据。</p>
</template>
