<!-- apps/frontend/src/features/dashboard/components/BaseChart.vue -->
<script setup lang="ts">
defineProps<{ title: string; empty?: boolean }>();
</script>

<template>
  <div class="card">
    <h3>{{ title }}</h3>
    <p v-if="empty" class="empty">No data yet.</p>
    <slot v-else />
  </div>
</template>

<style scoped>
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md, 12px);
  padding: var(--space-md);
}
.card h3 { margin: 0 0 var(--space-sm); font-size: 0.9rem; font-weight: 600; }
.empty { color: var(--text-muted); font-size: 0.85rem; margin: 0; }
</style>
