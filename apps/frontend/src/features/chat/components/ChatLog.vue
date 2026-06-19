<script setup lang="ts">
import { renderMarkdown } from '@/features/chat/markdown';
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
      <!-- Assistant/agent replies are markdown (links, bold); render them
           safely. User/system text stays literal. -->
      <p
        v-if="msg.role === 'assistant' || msg.role === 'agent'"
        class="text md"
        v-html="renderMarkdown(msg.text)"
      ></p>
      <p v-else class="text">{{ msg.text }}</p>
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

/* Markdown-rendered replies: marked emits its own block elements, so drop the
   pre-wrap used for literal text and style the generated nodes instead. */
.text.md {
  white-space: normal;
}
.text.md :first-child { margin-top: 0; }
.text.md :last-child { margin-bottom: 0; }
.text.md :where(p, ul, ol) { margin: 0.5rem 0; }
.text.md :where(ul, ol) { padding-left: 1.25rem; }
.text.md a {
  color: var(--assistant);
  text-decoration: underline;
  word-break: break-word;
}
.text.md a:hover { text-decoration: none; }
.text.md code {
  background: var(--surface);
  padding: 0.1rem 0.3rem;
  border-radius: var(--radius-sm, 4px);
  font-size: 0.9em;
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

.msg.agent .who { color: #c4b5fd; }
.msg.agent .text {
  background: linear-gradient(180deg, rgba(168, 85, 247, 0.1), rgba(168, 85, 247, 0.04));
  border-color: rgba(168, 85, 247, 0.45);
}
</style>
