<script setup lang="ts">
import { ref } from 'vue';
import { useChatStore } from '@/features/chat/store/chat.store';

const chat = useChatStore();
const draft = ref<string>('');

async function submit(): Promise<void> {
  if (!draft.value.trim() || chat.isSending) return;
  const text = draft.value;
  draft.value = '';
  await chat.send(text);
}

function onKeyDown(e: KeyboardEvent): void {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    submit();
  }
}
</script>

<template>
  <div class="input-row">
    <textarea
      v-model="draft"
      placeholder="Type a message..."
      rows="2"
      :disabled="chat.isSending"
      @keydown="onKeyDown"
    />
    <button
      class="send"
      :disabled="chat.isSending || !draft.trim()"
      @click="submit"
    >
      {{ chat.isSending ? '…' : 'Send' }}
    </button>
  </div>
</template>

<style scoped>
.input-row {
  display: flex;
  gap: var(--space-sm);
}

textarea {
  flex: 1;
  background: var(--surface);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: 0.7rem;
  font-size: 0.95rem;
  resize: vertical;
  min-height: 56px;
}

textarea:focus {
  outline: 2px solid var(--accent);
  outline-offset: -2px;
}

.send {
  background: var(--accent);
  color: var(--bg);
  border: none;
  border-radius: var(--radius-md);
  padding: 0 var(--space-lg);
  font-weight: 600;
  font-size: 0.9rem;
  min-width: 5rem;
  transition: background 120ms ease;
}

.send:hover:not(:disabled) {
  background: var(--accent-hover);
}

.send:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
