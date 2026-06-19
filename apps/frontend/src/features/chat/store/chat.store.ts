import { defineStore } from 'pinia';
import { computed, ref } from 'vue';
import { openAgentStream, postChatTurn } from '@/features/chat/api/chat.api';
import type {
  AgentMessageEvent,
  ChatMessage,
  HandoffPayload,
} from '@/features/chat/types';

export const useChatStore = defineStore('chat', () => {
  const sessionId = ref<string>(`sim-${Math.floor(Math.random() * 9999)}`);
  const messages = ref<ChatMessage[]>([
    {
      role: 'system',
      text: 'Send a message to talk to the agent. Switch to the Voice tab to hold the microphone and talk instead.',
    },
  ]);
  const isSending = ref<boolean>(false);
  const handoff = ref<HandoffPayload | null>(null);

  // Computed: an agent stream is live whenever we hold a handoff payload that
  // advertised live_chat_available.
  const isLiveChatActive = computed(
    () => handoff.value !== null && handoff.value.live_chat_available,
  );

  let agentStream: EventSource | null = null;

  function closeAgentStream(): void {
    if (agentStream) {
      agentStream.close();
      agentStream = null;
    }
  }

  function attachAgentStream(): void {
    closeAgentStream();
    const source = openAgentStream(sessionId.value);
    source.addEventListener('agent_message', (e: MessageEvent<string>) => {
      try {
        const evt = JSON.parse(e.data) as AgentMessageEvent;
        messages.value.push({
          role: 'agent',
          text: evt.text,
          meta: evt.author_name,
        });
      } catch {
        // Ignore malformed events; the stream itself is still healthy.
      }
    });
    source.addEventListener('error', () => {
      // EventSource auto-reconnects on transient errors; only act on a
      // permanently closed stream (readyState === CLOSED).
      if (source.readyState === EventSource.CLOSED) {
        messages.value.push({
          role: 'system',
          text: 'Live agent connection lost — refresh to reconnect.',
          meta: 'warning',
        });
      }
    });
    agentStream = source;
  }

  function resetSession(): void {
    closeAgentStream();
    sessionId.value = `sim-${Math.floor(Math.random() * 9999)}`;
    messages.value = [
      { role: 'system', text: `New session: ${sessionId.value}` },
    ];
    handoff.value = null;
  }

  async function send(text: string): Promise<void> {
    const trimmed = text.trim();
    if (!trimmed) return;
    messages.value.push({ role: 'user', text: trimmed });
    isSending.value = true;
    try {
      const result = await postChatTurn(sessionId.value, trimmed);

      // Handoff just happened: install the system marker, open the agent
      // stream if the backend brought a live bridge up.
      if (result.handoff) {
        const summary = result.handoff.summary ?? result.handoff.reason;
        messages.value.push({
          role: 'system',
          text: result.handoff.live_chat_available
            ? `Connected to a human agent. ${summary}`
            : `Escalated to a human agent. ${summary} (No live chat available — they'll respond via the ticket.)`,
          meta: `urgency=${result.handoff.urgency} · lang=${result.handoff.language}`,
        });
        handoff.value = result.handoff;
        if (result.handoff.live_chat_available) attachAgentStream();
        return;
      }

      // Mid-handoff turns: the message was relayed to the agent — no AI reply.
      if (result.forwarded_to_agent) {
        return;
      }

      if (result.reply) {
        const metaBits: string[] = [];
        if (result.language && result.language !== 'unknown') metaBits.push(`lang=${result.language}`);
        if (result.sentiment) metaBits.push(`sentiment=${result.sentiment}`);
        messages.value.push({
          role: 'assistant',
          text: result.reply,
          meta: metaBits.length ? metaBits.join(' · ') : undefined,
          products: result.products?.length ? result.products : undefined,
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

  return {
    sessionId,
    messages,
    isSending,
    handoff,
    isLiveChatActive,
    send,
    resetSession,
  };
});
