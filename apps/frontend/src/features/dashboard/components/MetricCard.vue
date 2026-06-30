<!-- apps/frontend/src/features/dashboard/components/MetricCard.vue -->
<script setup lang="ts">
defineProps<{ label: string; value: string }>();
</script>

<template>
  <div class="kpi">
    <span class="value">{{ value }}</span>
    <span class="label">{{ label }}</span>
  </div>
</template>

<style scoped>
.kpi {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md, 12px);
  padding: var(--space-md);
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}
.value { font-size: 1.6rem; font-weight: 700; color: var(--text); }
.label { font-size: 0.8rem; color: var(--text-muted); }
</style>
