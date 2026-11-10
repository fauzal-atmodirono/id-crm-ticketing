<script setup lang="ts">
// Chat / Voice tabs "Talk as" picker (brief 6). Same personas as the Phone
// tab's "Call as"; Anonymous is the default and sends no number. Changing it
// starts a new session (the store's setPersona).
import { computed } from 'vue'
import { CHAT_PERSONAS, type ChatPersona } from '../personas'

const props = defineProps<{ modelValue: string }>()
const emit = defineEmits<{ 'update:modelValue': [id: string] }>()

const chosen = computed<ChatPersona>(
  () => CHAT_PERSONAS.find((p) => p.id === props.modelValue) ?? (CHAT_PERSONAS[0] as ChatPersona),
)

function onChange(e: Event): void {
  emit('update:modelValue', (e.target as HTMLSelectElement).value)
}
</script>

<template>
  <label class="talk-as">
    <span class="talk-as__label">Talk as</span>
    <select class="talk-as__select" :value="modelValue" @change="onChange">
      <option v-for="p in CHAT_PERSONAS" :key="p.id" :value="p.id">{{ p.label }}</option>
    </select>
    <span class="talk-as__hint">{{ chosen.description }}</span>
  </label>
</template>

<style scoped>
.talk-as {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  font-size: 0.8rem;
  color: var(--text-muted);
}
.talk-as__select {
  padding: 0.45rem 0.5rem;
  font-size: 0.85rem;
  color: var(--text);
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
}
.talk-as__hint {
  font-size: 0.78rem;
}
</style>
