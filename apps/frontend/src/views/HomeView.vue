<script setup lang="ts">
import { ref } from 'vue';
import ChannelTabs, { type Channel } from '@/components/ui/ChannelTabs.vue';
import { ChatLog, ChatInput, useChatStore } from '@/features/chat';
import { VoiceLog, VoiceRecorder, useVoiceStore } from '@/features/voice';

const channel = ref<Channel>('chat');
const chat = useChatStore();
const voice = useVoiceStore();

function reset(): void {
  if (channel.value === 'chat') chat.resetSession();
  else voice.resetSession();
}
</script>

<template>
  <ChannelTabs v-model="channel" />

  <ChatLog v-if="channel === 'chat'" />
  <VoiceLog v-else />

  <ChatInput v-if="channel === 'chat'" />
  <VoiceRecorder v-else />

  <footer class="session-row">
    <span>
      Session:
      <code>{{ channel === 'chat' ? chat.sessionId : voice.sessionId }}</code>
    </span>
    <button type="button" @click="reset">New session</button>
  </footer>
</template>

<style scoped>
.session-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 0.85rem;
  color: var(--text-muted);
  gap: var(--space-md);
  flex-wrap: wrap;
}

code {
  background: var(--surface);
  padding: 0.15rem 0.4rem;
  border-radius: var(--radius-sm);
  font-family: var(--font-mono);
}

button {
  background: transparent;
  border: 1px solid var(--border);
  color: var(--text-muted);
  padding: 0.25rem 0.75rem;
  border-radius: var(--radius-sm);
  font-size: 0.8rem;
  transition: all 120ms ease;
}

button:hover {
  border-color: var(--text-muted);
  color: var(--text);
}
</style>
