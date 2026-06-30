<!-- apps/frontend/src/features/dashboard/components/ChannelFilter.vue -->
<script setup lang="ts">
defineProps<{ modelValue: string; channels: string[] }>();
defineEmits<{ 'update:modelValue': [value: string] }>();
</script>

<template>
  <select
    class="filter"
    :value="modelValue"
    @change="$emit('update:modelValue', ($event.target as HTMLSelectElement).value)"
  >
    <option v-for="c in channels" :key="c" :value="c">
      {{ c === 'all' ? 'All channels' : c }}
    </option>
  </select>
</template>

<style scoped>
.filter {
  background: var(--surface);
  border: 1px solid var(--border);
  color: var(--text);
  border-radius: var(--radius-full);
  padding: 0.3rem 0.75rem;
  font-size: 0.85rem;
}
</style>
