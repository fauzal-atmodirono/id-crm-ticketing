import { defineStore } from 'pinia';
import { ref } from 'vue';
import { openAgentStream } from '@/features/chat/api/chat.api';
import type { AgentMessageEvent } from '@/features/chat/types';
import { postVoiceTurn, postVoiceTts } from '@/features/voice/api/voice.api';
import type { HandoffPayload } from '@/features/chat/types';
import type { VoiceEntry } from '@/features/voice/types';

export type ConversationPhase = 'idle' | 'listening' | 'processing' | 'speaking';

export const useVoiceStore = defineStore('voice', () => {
  const sessionId = ref<string>(`voice-${Math.floor(Math.random() * 9999)}`);
  const entries = ref<VoiceEntry[]>([
    {
      kind: 'system',
      text: 'Tap the microphone and start talking — I’ll reply as soon as you pause.',
    },
  ]);
  const isSending = ref<boolean>(false);
  const phase = ref<ConversationPhase>('idle');
  const handoff = ref<HandoffPayload | null>(null);

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
    source.addEventListener('agent_message', async (e: MessageEvent<string>) => {
      try {
        const evt = JSON.parse(e.data) as AgentMessageEvent;
        phase.value = 'processing';
        
        // Fetch synthesized audio for the agent text
        const audioBlob = await postVoiceTts(evt.text, 'en-US');
        const replyUrl = URL.createObjectURL(audioBlob);

        entries.value.push({
          kind: 'assistant',
          text: evt.text,
          audioUrl: replyUrl,
          meta: evt.author_name,
        });

        phase.value = 'speaking';
        const audio = new Audio(replyUrl);
        audio.addEventListener('ended', () => {
          if (phase.value === 'speaking') phase.value = 'idle';
        });
        audio.addEventListener('error', () => {
          if (phase.value === 'speaking') phase.value = 'idle';
        });
        await audio.play();
      } catch (err) {
        phase.value = 'idle';
      }
    });
    
    source.addEventListener('error', () => {
      if (source.readyState === EventSource.CLOSED) {
        entries.value.push({
          kind: 'system',
          text: 'Live agent connection lost — refresh to reconnect.',
        });
      }
    });
    
    agentStream = source;
  }

  function resetSession(): void {
    closeAgentStream();
    sessionId.value = `voice-${Math.floor(Math.random() * 9999)}`;
    entries.value = [{ kind: 'system', text: `New session: ${sessionId.value}` }];
    phase.value = 'idle';
    handoff.value = null;
  }

  function setPhase(next: ConversationPhase): void {
    phase.value = next;
  }

  async function submitAudio(blob: Blob): Promise<void> {
    if (blob.size === 0) {
      entries.value.push({ kind: 'system', text: 'Recording was empty.' });
      phase.value = 'idle';
      return;
    }
    const userAudioUrl = URL.createObjectURL(blob);
    entries.value.push({
      kind: 'user',
      text: 'Voice message',
      audioUrl: userAudioUrl,
    });
    isSending.value = true;
    phase.value = 'processing';
    try {
      const result = await postVoiceTurn(sessionId.value, blob);
      
      // Update user voice entry to show the transcribed text if available
      if (result.userTranscription) {
        const userEntry = [...entries.value].reverse().find(e => e.kind === 'user');
        if (userEntry) {
          userEntry.text = result.userTranscription;
        }
      }

      if (result.handoff) {
        const summary = result.handoff.summary ?? result.handoff.reason;
        entries.value.push({
          kind: 'system',
          text: `Escalated to a human agent. ${summary}`,
        });
        handoff.value = result.handoff;
        phase.value = 'idle';
        if (result.handoff.live_chat_available) {
          attachAgentStream();
        }
      } else if (result.forwardedToAgent) {
        phase.value = 'idle';
      } else if (result.audioBlob.size > 0) {
        const replyUrl = URL.createObjectURL(result.audioBlob);
        entries.value.push({
          kind: 'assistant',
          text: result.replyText || '[audio reply]',
          audioUrl: replyUrl,
        });
        phase.value = 'speaking';
        const audio = new Audio(replyUrl);
        audio.addEventListener('ended', () => {
          if (phase.value === 'speaking') phase.value = 'idle';
        });
        audio.addEventListener('error', () => {
          if (phase.value === 'speaking') phase.value = 'idle';
        });
        audio.play().catch(() => {
          phase.value = 'idle';
        });
      } else {
        entries.value.push({
          kind: 'system',
          text: '(no audio reply — AI may be paused or the model returned nothing)',
          meta: result.replyText || undefined,
        });
        phase.value = 'idle';
      }
    } catch (e) {
      entries.value.push({
        kind: 'system',
        text: e instanceof Error ? e.message : 'Unknown voice error',
        meta: 'error',
      });
      phase.value = 'idle';
    } finally {
      isSending.value = false;
    }
  }

  return {
    sessionId,
    entries,
    isSending,
    phase,
    handoff,
    submitAudio,
    resetSession,
    setPhase,
  };
});
