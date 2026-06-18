<script setup lang="ts">
import { computed } from 'vue';
import { useMediaRecorder } from '@/features/voice/composables/useMediaRecorder';
import { useVoiceStore } from '@/features/voice/store/voice.store';

const voice = useVoiceStore();
const recorder = useMediaRecorder();

const label = computed<string>(() => {
  if (voice.isSending) return 'Sending…';
  if (recorder.isRecording.value) return 'Release to send';
  return 'Hold to talk';
});

async function onPointerDown(): Promise<void> {
  if (voice.isSending || recorder.isRecording.value) return;
  try {
    await recorder.start();
  } catch {
    // Error already surfaced via recorder.error
  }
}

async function onPointerUp(): Promise<void> {
  if (!recorder.isRecording.value) return;
  try {
    const blob = await recorder.stop();
    await voice.submitAudio(blob);
  } catch (e) {
    voice.entries.push({
      kind: 'system',
      text: e instanceof Error ? e.message : 'Recording failed',
      meta: 'error',
    });
  }
}
</script>

<template>
  <div class="recorder">
    <button
      class="mic"
      :class="{ recording: recorder.isRecording.value, sending: voice.isSending }"
      :disabled="voice.isSending"
      @pointerdown="onPointerDown"
      @pointerup="onPointerUp"
      @pointerleave="onPointerUp"
    >
      <span class="dot" />
      {{ label }}
    </button>
    <p v-if="recorder.error.value" class="err">{{ recorder.error.value }}</p>
  </div>
</template>

<style scoped>
.recorder {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-sm);
}

.mic {
  background: var(--surface);
  color: var(--text);
  border: 2px solid var(--border);
  border-radius: var(--radius-full);
  padding: 0.9rem 2rem;
  display: inline-flex;
  align-items: center;
  gap: var(--space-sm);
  font-weight: 600;
  font-size: 1rem;
  transition: all 120ms ease;
  user-select: none;
  touch-action: none;
}

.mic:hover:not(:disabled) {
  border-color: var(--accent);
}

.mic.recording {
  background: var(--danger);
  border-color: var(--danger);
  color: white;
  animation: pulse 1.2s ease infinite;
}

.mic.sending {
  opacity: 0.6;
  cursor: not-allowed;
}

.dot {
  width: 0.6rem;
  height: 0.6rem;
  border-radius: var(--radius-full);
  background: var(--text-muted);
  transition: background 120ms ease;
}

.mic.recording .dot {
  background: white;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.7; }
}

.err {
  color: var(--danger);
  font-size: 0.85rem;
  margin: 0;
}
</style>
