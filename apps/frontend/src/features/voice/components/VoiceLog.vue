<script setup lang="ts">
import { ref, watch, nextTick, onMounted } from 'vue';
import { useVoiceStore } from '@/features/voice/store/voice.store';

const voice = useVoiceStore();
const logEl = ref<HTMLElement | null>(null);
const currentlyPlayingUrl = ref<string | null>(null);
let activeAudio: HTMLAudioElement | null = null;

const playAudio = (url: string) => {
  if (activeAudio) {
    activeAudio.pause();
    activeAudio = null;
  }
  
  if (currentlyPlayingUrl.value === url) {
    currentlyPlayingUrl.value = null;
    return;
  }

  currentlyPlayingUrl.value = url;
  const audio = new Audio(url);
  activeAudio = audio;
  
  audio.addEventListener('ended', () => {
    if (currentlyPlayingUrl.value === url) {
      currentlyPlayingUrl.value = null;
    }
  });
  
  audio.addEventListener('error', () => {
    if (currentlyPlayingUrl.value === url) {
      currentlyPlayingUrl.value = null;
    }
  });
  
  audio.play().catch(() => {
    currentlyPlayingUrl.value = null;
  });
};

const scrollToBottom = () => {
  if (logEl.value) {
    logEl.value.scrollTop = logEl.value.scrollHeight;
  }
};

watch(
  () => voice.entries.length,
  async () => {
    await nextTick();
    scrollToBottom();
  }
);

onMounted(() => {
  scrollToBottom();
});
</script>

<template>
  <div ref="logEl" class="log">
    <article
      v-for="(entry, i) in voice.entries"
      :key="i"
      class="entry"
      :class="entry.kind"
    >
      <header class="who">{{ entry.kind }}</header>
      <div class="bubble-container">
        <p class="text">{{ entry.text }}</p>
        <button 
          v-if="entry.audioUrl" 
          class="play-btn" 
          :class="{ playing: currentlyPlayingUrl === entry.audioUrl }"
          @click="playAudio(entry.audioUrl)"
          title="Play audio"
        >
          <svg v-if="currentlyPlayingUrl === entry.audioUrl" xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="playing-waves"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon><path d="M15.54 8.46a5 5 0 0 1 0 7.07"></path><path d="M19.07 4.93a10 10 0 0 1 0 14.14"></path></svg>
          <svg v-else xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon><path d="M15.54 8.46a5 5 0 0 1 0 7.07"></path></svg>
        </button>
      </div>
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

.bubble-container {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  max-width: 100%;
}

.text {
  background: var(--bg);
  padding: 0.7rem 0.9rem;
  border-radius: var(--radius-md);
  white-space: pre-wrap;
  border: 1px solid var(--border);
  margin: 0;
  flex: 1;
}

.play-btn {
  background: transparent;
  border: none;
  cursor: pointer;
  color: var(--text-muted);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 6px;
  border-radius: 50%;
  border: 1px solid var(--border);
  transition: all 0.2s ease;
  flex-shrink: 0;
  height: 32px;
  width: 32px;
}

.play-btn:hover {
  background: rgba(255, 255, 255, 0.05);
  color: var(--assistant);
  border-color: var(--assistant);
}

.play-btn.playing {
  color: var(--assistant);
  border-color: var(--assistant);
  animation: pulse 1.5s infinite;
}

@keyframes pulse {
  0% { transform: scale(1); }
  50% { transform: scale(1.15); }
  100% { transform: scale(1); }
}

@keyframes wave {
  0% { opacity: 0.3; }
  50% { opacity: 1; }
  100% { opacity: 0.3; }
}

.playing-waves path {
  animation: wave 1s infinite alternate;
}
.playing-waves path:nth-child(2) {
  animation-delay: 0.2s;
}
.playing-waves path:nth-child(3) {
  animation-delay: 0.4s;
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
