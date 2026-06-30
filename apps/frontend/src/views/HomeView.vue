<script setup lang="ts">
import { ref } from 'vue';
import ChannelTabs, { type Channel } from '@/components/ui/ChannelTabs.vue';
import { ChatLog, ChatInput, useChatStore } from '@/features/chat';
import { VoiceLog, VoiceRecorder, useVoiceStore } from '@/features/voice';
import { PhoneCall } from '@/features/phone';

const channel = ref<Channel>('chat');
const chat = useChatStore();
const voice = useVoiceStore();

function reset(): void {
  if (channel.value === 'chat') chat.resetSession();
  else if (channel.value === 'voice') voice.resetSession();
}
</script>

<template>
  <div class="home-shell">
    <ChannelTabs v-model="channel" />

    <transition name="fade">
      <aside
        v-if="chat.handoff && channel === 'chat'"
        class="handoff-banner"
        :data-live="chat.isLiveChatActive ? 'true' : 'false'"
      >
        <span class="dot" />
        <div class="info">
          <strong>{{ chat.isLiveChatActive ? 'Connected to a human agent' : 'Handed off to a human agent' }}</strong>
          <span>
            {{ chat.handoff.summary || chat.handoff.reason }}
            <span class="pill">urgency · {{ chat.handoff.urgency }}</span>
          </span>
        </div>
      </aside>
    </transition>

    <ChatLog v-if="channel === 'chat'" />
    <VoiceLog v-else-if="channel === 'voice'" />

    <ChatInput v-if="channel === 'chat'" />
    <VoiceRecorder v-else-if="channel === 'voice'" />
    <PhoneCall v-else-if="channel === 'phone'" />

    <footer class="session-row">
      <span>
        Session:
        <code>{{ channel === 'chat' ? chat.sessionId : channel === 'voice' ? voice.sessionId : '—' }}</code>
      </span>
      <button type="button" @click="reset">New session</button>
    </footer>
  </div>
</template>

<style scoped>
.handoff-banner {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  background: linear-gradient(180deg, rgba(168, 85, 247, 0.12), rgba(168, 85, 247, 0.04));
  border: 1px solid rgba(168, 85, 247, 0.4);
  padding: 0.65rem 0.85rem;
  border-radius: var(--radius-md);
  font-size: 0.85rem;
  color: var(--text);
}
.handoff-banner[data-live='false'] {
  background: linear-gradient(180deg, rgba(234, 179, 8, 0.1), rgba(234, 179, 8, 0.03));
  border-color: rgba(234, 179, 8, 0.4);
}

.dot {
  width: 0.55rem;
  height: 0.55rem;
  border-radius: var(--radius-full);
  background: #a855f7;
  box-shadow: 0 0 0 4px rgba(168, 85, 247, 0.18);
  animation: blink 2s ease infinite;
}
.handoff-banner[data-live='false'] .dot {
  background: #eab308;
  box-shadow: 0 0 0 4px rgba(234, 179, 8, 0.18);
  animation: none;
}

.info {
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
}
.info strong {
  font-size: 0.88rem;
  font-weight: 600;
}
.info span {
  color: var(--text-muted);
  font-size: 0.78rem;
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  flex-wrap: wrap;
}

.pill {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-full);
  padding: 0.05rem 0.5rem;
  font-size: 0.7rem;
  text-transform: lowercase;
  color: var(--text);
}

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

.fade-enter-active,
.fade-leave-active { transition: opacity 200ms ease; }
.fade-enter-from,
.fade-leave-to { opacity: 0; }

@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.55; }
}

.home-shell {
  width: 100%;
  max-width: 820px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: var(--space-md);
}
</style>
