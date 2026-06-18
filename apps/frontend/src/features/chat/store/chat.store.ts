import { defineStore } from 'pinia';
import { ref } from 'vue';
import { postChatTurn } from '@/features/chat/api/chat.api';
import type { ChatMessage } from '@/features/chat/types';

export const useChatStore = defineStore('chat', () => {
  const sessionId = ref<string>(`sim-${Math.floor(Math.random() * 9999)}`);
  const messages = ref<ChatMessage[]>([
    {
      role: 'system',
      text: 'Send a message to talk to the agent. Switch to the Voice tab to hold the microphone and talk instead.',
    },
  ]);
  const isSending = ref<boolean>(false);

  function resetSession(): void {
    sessionId.value = `sim-${Math.floor(Math.random() * 9999)}`;
    messages.value = [
      { role: 'system', text: `New session: ${sessionId.value}` },
    ];
  }

  async function send(text: string): Promise<void> {
    const trimmed = text.trim();
    if (!trimmed) return;
    messages.value.push({ role: 'user', text: trimmed });
    isSending.value = true;
    try {
      const result = await postChatTurn(sessionId.value, trimmed);
      if (result.handoff) {
        const summary = result.handoff.summary ?? result.handoff.reason;
        messages.value.push({
          role: 'system',
          text: `Escalated to human agent: ${summary}`,
          meta: `urgency=${result.handoff.urgency} · lang=${result.handoff.language}`,
        });
      } else if (result.reply) {
        const metaBits: string[] = [];
        if (result.language && result.language !== 'unknown') metaBits.push(`lang=${result.language}`);
        if (result.sentiment) metaBits.push(`sentiment=${result.sentiment}`);
        messages.value.push({
          role: 'assistant',
          text: result.reply,
          meta: metaBits.length ? metaBits.join(' · ') : undefined,
        });
      } else {
        messages.value.push({
          role: 'system',
          text: '(no reply — AI may be paused or response was empty)',
        });
      }
    } catch (e) {
      messages.value.push({
        role: 'system',
        text: e instanceof Error ? e.message : 'Unknown error',
        meta: 'error',
      });
    } finally {
      isSending.value = false;
    }
  }

  return { sessionId, messages, isSending, send, resetSession };
});
