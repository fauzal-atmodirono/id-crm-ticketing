<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useVoiceCapture } from '@/features/voice/composables/useVoiceCapture';
import { useVoiceStore } from '@/features/voice/store/voice.store';
import WaveformDisplay from './WaveformDisplay.vue';

const voice = useVoiceStore();
const capture = useVoiceCapture();

const isContinuousMode = ref<boolean>(false);

const buttonLabel = computed<string>(() => {
  if (capture.isActive.value && voice.phase !== 'speaking') {
    if (capture.status.value === 'finalizing') return 'Sending…';
    if (capture.status.value === 'silence') return 'Heard you. Wrapping up…';
    if (capture.hasSpeech.value) return 'Listening — tap to send';
    return 'Listening — speak up';
  }
  if (voice.phase === 'processing') return 'Thinking…';
  if (voice.phase === 'speaking') return 'Replying — speak to interrupt';
  return 'Tap to talk';
});

const statusText = computed<string>(() => {
  if (capture.error.value) return capture.error.value;
  if (capture.isActive.value && voice.phase !== 'speaking') {
    if (capture.status.value === 'silence') return 'Pause detected — finishing turn…';
    if (capture.hasSpeech.value) return 'I’ll auto-send when you stop talking.';
    return 'Waiting for your voice…';
  }
  if (voice.phase === 'processing') return 'Sending audio to the assistant…';
  if (voice.phase === 'speaking') return 'Assistant is replying. Speak to interrupt.';
  return 'Tap the mic and start talking. I’ll reply with text + voice.';
});

const isHot = computed<boolean>(
  () => capture.hasSpeech.value && capture.status.value !== 'silence' && voice.phase !== 'speaking',
);

async function onMicClick(): Promise<void> {
  // If session is active, stop it all and go back to idle.
  if (isContinuousMode.value || voice.phase !== 'idle' || capture.isActive.value) {
    isContinuousMode.value = false;
    voice.stopAssistantAudio();
    capture.cancel();
    voice.setPhase('idle');
    return;
  }

  // Otherwise, start a continuous voice session
  try {
    isContinuousMode.value = true;
    voice.setPhase('listening');
    await capture.start((wav) => {
      void voice.submitAudio(wav);
    });
  } catch {
    voice.setPhase('idle');
    isContinuousMode.value = false;
  }
}

// Watch voice phase to manage continuous listening and barge-in activation
watch(
  () => voice.phase,
  async (newPhase, oldPhase) => {
    if (newPhase === 'speaking') {
      // Start microphone monitoring for barge-in
      try {
        await capture.start((_wav) => {
          // Discard output of barge-in monitor
        });
      } catch (err) {
        console.warn('Failed to start microphone for barge-in monitoring:', err);
      }
    } else if (newPhase === 'idle') {
      // Auto-restart listening if continuous mode is active
      if ((oldPhase === 'speaking' || oldPhase === 'processing') && isContinuousMode.value) {
        voice.setPhase('listening');
        try {
          await capture.start((wav) => {
            void voice.submitAudio(wav);
          });
        } catch {
          voice.setPhase('idle');
        }
      }
    }
  }
);

// Watch microphone audio levels during speaking phase for barge-in (interruption)
watch(capture.level, async (newLevel) => {
  if (voice.phase === 'speaking' && newLevel > 0.045) {
    // User spoke over the assistant -> trigger barge-in interruption!
    voice.stopAssistantAudio();
    capture.cancel();

    // Immediately start fresh recording capture for user speech
    voice.setPhase('listening');
    try {
      await capture.start((wav) => {
        void voice.submitAudio(wav);
      });
    } catch {
      voice.setPhase('idle');
    }
  }
});

function onKeydown(e: KeyboardEvent): void {
  if (e.code !== 'Space' || e.repeat) return;
  const target = e.target as HTMLElement | null;
  if (
    target &&
    (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable)
  ) {
    return;
  }
  e.preventDefault();
  void onMicClick();
}

onMounted(() => window.addEventListener('keydown', onKeydown));
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown);
  isContinuousMode.value = false;
  voice.stopAssistantAudio();
  capture.cancel();
});
</script>

<template>
  <div class="recorder">
    <WaveformDisplay
      :analyser="capture.analyser.value"
      :active="capture.isActive.value"
      :hot="isHot"
    />

    <button
      type="button"
      class="mic"
      :class="{
        listening: capture.isActive.value && voice.phase !== 'speaking',
        hot: isHot,
        processing: voice.phase === 'processing',
        speaking: voice.phase === 'speaking',
      }"
      :disabled="voice.phase === 'processing'"
      :aria-pressed="capture.isActive.value"
      @click="onMicClick"
    >
      <span class="ring" :style="{ transform: `scale(${1 + capture.level.value * 1.6})` }" />
      <span class="icon" aria-hidden="true">
        <svg viewBox="0 0 24 24" width="22" height="22" fill="currentColor">
          <path d="M12 14a3 3 0 0 0 3-3V6a3 3 0 0 0-6 0v5a3 3 0 0 0 3 3z" />
          <path
            d="M19 11a1 1 0 0 0-2 0 5 5 0 0 1-10 0 1 1 0 0 0-2 0 7 7 0 0 0 6 6.92V20H8a1 1 0 0 0 0 2h8a1 1 0 0 0 0-2h-3v-2.08A7 7 0 0 0 19 11z"
          />
        </svg>
      </span>
      <span class="label">{{ buttonLabel }}</span>
    </button>

    <p class="hint" :class="{ error: !!capture.error.value }">{{ statusText }}</p>
    <p class="kbd"><kbd>Space</kbd> toggles the mic</p>
  </div>
</template>

<style scoped>
.recorder {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: var(--space-md);
}

.mic {
  position: relative;
  align-self: center;
  display: inline-flex;
  align-items: center;
  gap: 0.75rem;
  background: var(--surface);
  color: var(--text);
  border: 2px solid var(--border);
  border-radius: var(--radius-full);
  padding: 0.85rem 1.6rem 0.85rem 1.2rem;
  font-weight: 600;
  font-size: 1rem;
  cursor: pointer;
  transition:
    background 160ms ease,
    border-color 160ms ease,
    color 160ms ease,
    transform 120ms ease,
    box-shadow 200ms ease;
  user-select: none;
  isolation: isolate;
  overflow: hidden;
}
.mic:hover:not(:disabled) {
  border-color: var(--accent);
}
.mic:active {
  transform: scale(0.98);
}
.mic:disabled {
  cursor: progress;
  opacity: 0.8;
}

.mic .icon {
  position: relative;
  z-index: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 1.6rem;
  height: 1.6rem;
  border-radius: var(--radius-full);
  background: var(--bg);
  color: var(--text);
}
.mic .label {
  position: relative;
  z-index: 1;
}

/* Pulsing halo locked to current mic level */
.mic .ring {
  position: absolute;
  inset: 0;
  border-radius: var(--radius-full);
  background: radial-gradient(circle at 22px 50%, rgba(34, 211, 238, 0.55), transparent 55%);
  opacity: 0;
  transition: opacity 200ms ease;
  pointer-events: none;
  z-index: 0;
}

.mic.listening {
  border-color: rgba(34, 211, 238, 0.65);
  color: #e6fbff;
  background: linear-gradient(180deg, rgba(34, 211, 238, 0.16), rgba(34, 211, 238, 0.06));
}
.mic.listening .ring {
  opacity: 1;
}
.mic.listening .icon {
  background: #22d3ee;
  color: #001318;
}
.mic.hot {
  box-shadow: 0 0 0 6px rgba(34, 211, 238, 0.18);
  border-color: rgba(34, 211, 238, 1);
}

.mic.processing {
  border-color: var(--text-muted);
  background: var(--surface);
}
.mic.processing .icon {
  animation: spin 1s linear infinite;
}

.mic.speaking {
  border-color: rgba(168, 85, 247, 0.65);
  background: linear-gradient(180deg, rgba(168, 85, 247, 0.16), rgba(168, 85, 247, 0.06));
  color: #f3e8ff;
}
.mic.speaking .icon {
  background: #a855f7;
  color: white;
  animation: pulse 1.1s ease infinite;
}

.hint {
  margin: 0;
  text-align: center;
  font-size: 0.9rem;
  color: var(--text-muted);
  min-height: 1.2em;
}
.hint.error {
  color: var(--danger);
}

.kbd {
  margin: 0;
  text-align: center;
  font-size: 0.72rem;
  color: var(--text-muted);
  opacity: 0.7;
}
.kbd kbd {
  background: var(--surface);
  border: 1px solid var(--border);
  border-bottom-width: 2px;
  border-radius: 4px;
  padding: 0 0.35rem;
  font-family: var(--font-mono);
  font-size: 0.7rem;
}

@keyframes pulse {
  0%, 100% { transform: scale(1); }
  50% { transform: scale(1.12); }
}
@keyframes spin {
  to { transform: rotate(360deg); }
}
</style>
