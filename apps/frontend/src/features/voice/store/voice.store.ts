import { defineStore } from 'pinia';
import { ref } from 'vue';
import { postVoiceTurn } from '@/features/voice/api/voice.api';
import type { VoiceEntry } from '@/features/voice/types';

export const useVoiceStore = defineStore('voice', () => {
  const sessionId = ref<string>(`voice-${Math.floor(Math.random() * 9999)}`);
  const entries = ref<VoiceEntry[]>([
    {
      kind: 'system',
      text: 'Hold the microphone button and speak. The agent hears your audio directly and replies with synthesized speech.',
    },
  ]);
  const isSending = ref<boolean>(false);

  function resetSession(): void {
    sessionId.value = `voice-${Math.floor(Math.random() * 9999)}`;
    entries.value = [
      { kind: 'system', text: `New session: ${sessionId.value}` },
    ];
  }

  async function submitAudio(blob: Blob): Promise<void> {
    if (blob.size === 0) {
      entries.value.push({ kind: 'system', text: 'Recording was empty.' });
      return;
    }
    const userAudioUrl = URL.createObjectURL(blob);
    entries.value.push({
      kind: 'user',
      text: `[audio · ${Math.round(blob.size / 1024)} kB]`,
      audioUrl: userAudioUrl,
    });
    isSending.value = true;
    try {
      const result = await postVoiceTurn(sessionId.value, blob);
      if (result.handoffReason) {
        entries.value.push({
          kind: 'system',
          text: `Escalated to human agent (${result.handoffReason}).`,
        });
      } else if (result.audioBlob.size > 0) {
        const replyUrl = URL.createObjectURL(result.audioBlob);
        entries.value.push({
          kind: 'assistant',
          text: result.replyText || '[audio reply]',
          audioUrl: replyUrl,
        });
        // Autoplay the reply
        new Audio(replyUrl).play().catch(() => undefined);
      } else {
        entries.value.push({
          kind: 'system',
          text: '(no audio reply — AI may be paused or the model returned nothing)',
          meta: result.replyText || undefined,
        });
      }
    } catch (e) {
      entries.value.push({
        kind: 'system',
        text: e instanceof Error ? e.message : 'Unknown voice error',
        meta: 'error',
      });
    } finally {
      isSending.value = false;
    }
  }

  return { sessionId, entries, isSending, submitAudio, resetSession };
});
