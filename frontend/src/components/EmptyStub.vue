<script setup lang="ts">
defineProps<{ feature?: string }>()
</script>

<template>
  <a-empty description="规划中 — 该模块尚未实现（待 P2/P3）">
    <template v-if="feature">
      <p>feature: {{ feature }}</p>
    </template>
  </a-empty>
</template>
