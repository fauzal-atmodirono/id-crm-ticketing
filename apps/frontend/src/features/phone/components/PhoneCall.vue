<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { Device, type Call } from '@twilio/voice-sdk'
import { fetchPhoneToken } from '../api/phone.api'
import PhoneVisualizer from './PhoneVisualizer.vue'

type Status = 'idle' | 'connecting' | 'in-call' | 'error'

const status = ref<Status>('idle')
const elapsed = ref(0)
const analyser = ref<AnalyserNode | null>(null)

let device: Device | null = null
let call: Call | null = null
let audioCtx: AudioContext | null = null
let timer: number | null = null

const elapsedLabel = computed(() => {
  const m = Math.floor(elapsed.value / 60)
  const s = elapsed.value % 60
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
})

function startTimer(): void {
  elapsed.value = 0
  timer = window.setInterval(() => (elapsed.value += 1), 1000)
}

function stopTimer(): void {
  if (timer != null) {
    clearInterval(timer)
    timer = null
  }
}

// Tap Twilio's remote (AI) audio into an AnalyserNode so the bars react to the
// agent's voice. Analysis only — never connect to destination, since Twilio
// already plays the remote audio (connecting would double it). Falls back to the
// visualizer's ambient animation if the stream isn't exposed.
function attachAnalyser(): void {
  if (analyser.value) return
  try {
    const withStream = call as (Call & { getRemoteStream?: () => MediaStream | null }) | null
    const stream = withStream?.getRemoteStream?.()
    if (!stream) return
    const Ctx =
      window.AudioContext ??
      (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext
    audioCtx = new Ctx()
    const node = audioCtx.createAnalyser()
    node.fftSize = 256
    audioCtx.createMediaStreamSource(stream).connect(node)
    analyser.value = node
  } catch {
    analyser.value = null
  }
}

async function startCall(): Promise<void> {
  if (status.value === 'connecting' || status.value === 'in-call') return
  try {
    status.value = 'connecting'
    const { token } = await fetchPhoneToken()
    device = new Device(token)
    call = await device.connect()
    call.on('disconnect', endCall)
    call.on('accept', attachAnalyser)
    status.value = 'in-call'
    startTimer()
    attachAnalyser()
  } catch {
    status.value = 'error'
  }
}

function endCall(): void {
  stopTimer()
  analyser.value = null
  void audioCtx?.close().catch(() => undefined)
  audioCtx = null
  call?.disconnect()
  device?.destroy()
  call = null
  device = null
  status.value = 'idle'
}

onBeforeUnmount(endCall)
</script>

<template>
  <div class="phone" :class="`phone--${status}`">
    <template v-if="status === 'idle' || status === 'error'">
      <div class="phone__head">
        <span class="phone__title">Talk to Proton support</span>
        <span class="phone__sub">Real-time voice, answered instantly by AI</span>
      </div>
      <button class="btn btn--call" type="button" @click="startCall">
        <span class="btn__glyph" aria-hidden="true">📞</span>
        {{ status === 'error' ? 'Try again' : 'Call support' }}
      </button>
      <p v-if="status === 'error'" class="phone__err" role="alert">
        Couldn’t connect the call. Check your microphone permission and try again.
      </p>
    </template>

    <template v-else-if="status === 'connecting'">
      <div class="phone__connecting">
        <span class="ring" aria-hidden="true"></span>
        <span class="phone__connecting-text">Connecting<i class="ellipsis"></i></span>
      </div>
      <button class="btn btn--ghost" type="button" @click="endCall">Cancel</button>
    </template>

    <template v-else>
      <div class="phone__head">
        <span class="phone__live">
          <span class="live-dot" aria-hidden="true"></span> On call · {{ elapsedLabel }}
        </span>
        <span class="phone__agent">Proton AI</span>
      </div>
      <PhoneVisualizer :analyser="analyser" :active="true" />
      <button class="btn btn--hangup" type="button" @click="endCall">
        <span class="btn__glyph" aria-hidden="true">⛔</span> Hang up
      </button>
    </template>
  </div>
</template>

<style scoped>
.phone {
  display: flex;
  flex-direction: column;
  gap: var(--space-md);
  width: 100%;
  max-width: 340px;
  padding: var(--space-md) var(--space-md);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  background: var(--surface);
  color: var(--text);
}
.phone--in-call {
  border-color: var(--accent);
  box-shadow: 0 12px 34px -16px rgba(56, 189, 248, 0.5);
}

.phone__head {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
}
.phone__title {
  font-size: 1rem;
  font-weight: 650;
  letter-spacing: -0.01em;
}
.phone__sub {
  font-size: 0.8rem;
  color: var(--text-muted);
}

.phone__live {
  display: inline-flex;
  align-items: center;
  gap: 0.45rem;
  font-size: 0.85rem;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: var(--accent);
}
.phone__agent {
  font-size: 0.75rem;
  color: var(--text-muted);
}
.live-dot {
  width: 8px;
  height: 8px;
  border-radius: var(--radius-full);
  background: var(--danger);
  animation: live 1.6s ease-out infinite;
}

.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  padding: 0.7rem 1rem;
  font-size: 0.92rem;
  font-weight: 650;
  border: none;
  border-radius: var(--radius-md);
  color: var(--bg);
  transition: transform 0.08s ease, filter 0.15s ease;
}
.btn:active {
  transform: translateY(1px);
}
.btn:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
.btn:hover {
  filter: brightness(1.08);
}
.btn__glyph {
  font-size: 1.05rem;
  line-height: 1;
}
.btn--call {
  background: var(--success);
}
.btn--hangup {
  background: var(--danger);
}
.btn--ghost {
  background: transparent;
  color: var(--text-muted);
  border: 1px solid var(--border);
}

.phone__connecting {
  display: flex;
  align-items: center;
  gap: 0.7rem;
  min-height: 56px;
}
.phone__connecting-text {
  font-size: 0.9rem;
  font-weight: 600;
  color: var(--text);
}
.ring {
  width: 22px;
  height: 22px;
  border-radius: var(--radius-full);
  border: 2.5px solid var(--border);
  border-top-color: var(--accent);
  animation: spin 0.8s linear infinite;
}
.ellipsis::after {
  content: '';
  animation: dots 1.4s steps(4, end) infinite;
}

.phone__err {
  margin: 0;
  font-size: 0.8rem;
  color: var(--danger);
}

@keyframes live {
  0% { box-shadow: 0 0 0 0 rgba(248, 113, 113, 0.5); }
  70% { box-shadow: 0 0 0 7px rgba(248, 113, 113, 0); }
  100% { box-shadow: 0 0 0 0 rgba(248, 113, 113, 0); }
}
@keyframes spin {
  to { transform: rotate(360deg); }
}
@keyframes dots {
  0% { content: ''; }
  25% { content: '.'; }
  50% { content: '..'; }
  75% { content: '...'; }
}

@media (prefers-reduced-motion: reduce) {
  .live-dot,
  .ring,
  .ellipsis::after {
    animation: none;
  }
}
</style>
