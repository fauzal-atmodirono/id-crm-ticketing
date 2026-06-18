<script setup lang="ts">
export type Channel = 'chat' | 'voice';

defineProps<{ modelValue: Channel }>();
defineEmits<{ 'update:modelValue': [value: Channel] }>();
</script>

<template>
  <nav class="tabs" role="tablist">
    <button
      type="button"
      role="tab"
      :aria-selected="modelValue === 'chat'"
      :class="{ active: modelValue === 'chat' }"
      @click="$emit('update:modelValue', 'chat')"
    >
      Chat
    </button>
    <button
      type="button"
      role="tab"
      :aria-selected="modelValue === 'voice'"
      :class="{ active: modelValue === 'voice' }"
      @click="$emit('update:modelValue', 'voice')"
    >
      Voice
    </button>
  </nav>
</template>

<style scoped>
.tabs {
  display: flex;
  gap: var(--space-sm);
}

button {
  background: var(--surface);
  border: 1px solid var(--border);
  color: var(--text-muted);
  padding: 0.4rem 0.9rem;
  border-radius: var(--radius-full);
  font-size: 0.85rem;
  font-weight: 500;
  transition: all 120ms ease;
}

button:hover:not(.active) {
  color: var(--text);
  border-color: var(--text-muted);
}

button.active {
  background: var(--accent);
  border-color: var(--accent);
  color: var(--bg);
  font-weight: 600;
}
</style>
