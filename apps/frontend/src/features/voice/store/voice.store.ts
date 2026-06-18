import { defineStore } from 'pinia';
import { ref } from 'vue';
import { postVoiceTurn } from '@/features/voice/api/voice.api';
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

  function resetSession(): void {
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
      text: `[voice · ${Math.round(blob.size / 1024)} kB]`,
      audioUrl: userAudioUrl,
    });
    isSending.value = true;
    phase.value = 'processing';
    try {
      const result = await postVoiceTurn(sessionId.value, blob);
      if (result.handoff) {
        const summary = result.handoff.summary ?? result.handoff.reason;
        entries.value.push({
          kind: 'system',
          text:
            `Escalated to a human agent. ${summary}\n\n` +
            `Switch to the Chat tab to keep talking with them — the agent's replies appear there in real time.`,
        });
        handoff.value = result.handoff;
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
