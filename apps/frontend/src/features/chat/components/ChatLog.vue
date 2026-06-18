<script setup lang="ts">
import { useChatStore } from '@/features/chat/store/chat.store';

const chat = useChatStore();
</script>

<template>
  <div class="log">
    <article
      v-for="(msg, i) in chat.messages"
      :key="i"
      class="msg"
      :class="msg.role"
    >
      <header class="who">{{ msg.role }}</header>
      <p class="text">{{ msg.text }}</p>
      <footer v-if="msg.meta" class="meta">{{ msg.meta }}</footer>
    </article>
  </div>
</template>

<style scoped>
.log {
  flex: 1;
  min-height: 320px;
  max-height: 56vh;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: var(--space-md);
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: var(--space-md);
}

.msg {
  display: flex;
  flex-direction: column;
  gap: var(--space-xs);
  margin: 0;
}

.who {
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  font-weight: 600;
}

.text {
  background: var(--bg);
  padding: 0.7rem 0.9rem;
  border-radius: var(--radius-md);
  white-space: pre-wrap;
  border: 1px solid var(--border);
  line-height: 1.45;
  margin: 0;
}

.meta {
  font-size: 0.7rem;
  color: var(--text-muted);
  font-style: italic;
}

.msg.user .who { color: var(--user); }
.msg.assistant .who { color: var(--assistant); }
.msg.system .who { color: var(--text-muted); }
.msg.system .text {
  background: transparent;
  border-style: dashed;
  color: var(--text-muted);
  font-size: 0.85rem;
}
</style>
