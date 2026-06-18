<script setup lang="ts">
import { useVoiceStore } from '@/features/voice/store/voice.store';

const voice = useVoiceStore();
</script>

<template>
  <div class="log">
    <article
      v-for="(entry, i) in voice.entries"
      :key="i"
      class="entry"
      :class="entry.kind"
    >
      <header class="who">{{ entry.kind }}</header>
      <p class="text">{{ entry.text }}</p>
      <audio v-if="entry.audioUrl" :src="entry.audioUrl" controls />
      <footer v-if="entry.meta" class="meta">{{ entry.meta }}</footer>
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

.entry {
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
  margin: 0;
}

audio {
  width: 100%;
  margin-top: var(--space-xs);
}

.meta {
  font-size: 0.7rem;
  color: var(--text-muted);
  font-style: italic;
}

.entry.user .who { color: var(--user); }
.entry.assistant .who { color: var(--assistant); }
.entry.system .who { color: var(--text-muted); }
.entry.system .text {
  background: transparent;
  border-style: dashed;
  color: var(--text-muted);
  font-size: 0.85rem;
}
</style>
