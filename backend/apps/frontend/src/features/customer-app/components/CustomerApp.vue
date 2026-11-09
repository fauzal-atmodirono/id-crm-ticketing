<script setup lang="ts">
// Mock "Proton e.MAS customer app" (demo). One persona picker drives chat,
// photo/video, voice note and the web call, so one [DEMO] contact in the CRM
// collects the history from every channel (Customer 360).
//
// Chat goes through the backend's /customer-app/messages (posted into the
// Chatwoot API inbox the agent bot serves); the call reuses the Phone tab's
// web call-as path (Voice SDK `callAs` param, honoured by the backend only
// for browser calls with PHONE_WEB_CALL_AS_ENABLED).
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { Device, type Call } from '@twilio/voice-sdk'
import { PERSONAS, type Persona } from '@/features/phone/personas'
import { fetchPhoneToken } from '@/features/phone/api/phone.api'
import { useVoiceCapture } from '@/features/voice'
import {
  fetchThread,
  sendMessage,
  type CustomerMessage,
} from '../api/customer-app.api'

const POLL_MS = 3000
const MAX_UPLOAD_BYTES = 15 * 1024 * 1024

// Only personas with a number: the server refuses the others anyway.
const personas = PERSONAS.filter((p) => p.number !== '')
const personaId = ref('') // none selected: the user must pick
const persona = computed<Persona | null>(
  () => personas.find((p) => p.id === personaId.value) ?? null,
)

const messages = ref<CustomerMessage[]>([])
const draft = ref('')
const sending = ref(false)
const error = ref<string | null>(null)
const threadEl = ref<HTMLElement | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)

let pollTimer: number | null = null
let pollSeq = 0

function lastId(list: CustomerMessage[]): number {
  return list.length ? (list[list.length - 1]?.id ?? 0) : 0
}

async function refresh(): Promise<void> {
  const id = personaId.value
  if (!id) return
  const seq = ++pollSeq
  try {
    const thread = await fetchThread(id)
    if (seq !== pollSeq || id !== personaId.value) return // persona changed meanwhile
    const grew = lastId(thread.messages) !== lastId(messages.value)
    messages.value = thread.messages
    error.value = null
    if (grew) await scrollToEnd()
  } catch (e) {
    if (seq === pollSeq) error.value = e instanceof Error ? e.message : 'Could not load messages'
  }
}

async function scrollToEnd(): Promise<void> {
  await nextTick()
  const el = threadEl.value
  if (el) el.scrollTop = el.scrollHeight
}

function startPolling(): void {
  stopPolling()
  void refresh()
  pollTimer = window.setInterval(() => {
    if (document.visibilityState === 'visible') void refresh()
  }, POLL_MS)
}

function stopPolling(): void {
  if (pollTimer != null) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

watch(personaId, (id) => {
  endCall()
  voice.cancel()
  messages.value = []
  error.value = null
  if (id) startPolling()
  else stopPolling()
})

async function send(file?: Blob, filename?: string): Promise<void> {
  const id = personaId.value
  const text = draft.value.trim()
  if (!id || sending.value || (!text && !file)) return
  sending.value = true
  error.value = null
  try {
    await sendMessage(id, text, file, filename)
    draft.value = ''
    await refresh()
  } catch (e) {
    error.value = e instanceof Error ? `Not sent (${e.message})` : 'Not sent'
  } finally {
    sending.value = false
  }
}

function pickFile(): void {
  fileInput.value?.click()
}

async function onFile(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  if (file.size > MAX_UPLOAD_BYTES) {
    error.value = 'That file is over 15 MB.'
    return
  }
  await send(file, file.name)
}

// Voice note: recorded in the browser and re-encoded to 16 kHz mono WAV
// (Gemini does not take WebM). Stops on silence or when tapped again.
const voice = useVoiceCapture({ maxRecordingMs: 60_000, silenceMs: 2500 })

async function toggleVoiceNote(): Promise<void> {
  if (!personaId.value || sending.value) return
  if (voice.isActive.value) {
    await voice.stop()
    return
  }
  try {
    await voice.start((wav) => void send(wav, 'voice-note.wav'))
  } catch {
    error.value = voice.error.value ?? 'Microphone unavailable'
  }
}

// --- web call as the persona ------------------------------------------------
type CallStatus = 'idle' | 'connecting' | 'in-call' | 'error'
const callStatus = ref<CallStatus>('idle')
let device: Device | null = null
let call: Call | null = null

async function startCall(): Promise<void> {
  const p = persona.value
  if (!p || callStatus.value === 'connecting' || callStatus.value === 'in-call') return
  try {
    callStatus.value = 'connecting'
    const { token } = await fetchPhoneToken()
    device = new Device(token)
    call = await device.connect({ params: { callAs: p.number } })
    call.on('disconnect', endCall)
    call.on('accept', () => (callStatus.value = 'in-call'))
    callStatus.value = 'in-call'
  } catch {
    endCall()
    callStatus.value = 'error'
  }
}

function endCall(): void {
  call?.disconnect()
  device?.destroy()
  call = null
  device = null
  callStatus.value = 'idle'
}

const callIdle = computed(() => callStatus.value === 'idle' || callStatus.value === 'error')

onBeforeUnmount(() => {
  stopPolling()
  endCall()
  voice.cancel()
})

function timeLabel(at: number | null): string {
  if (!at) return ''
  return new Date(at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}
</script>

<template>
  <div class="device">
    <header class="app-bar">
      <div class="brand">
        <span class="brand__name">Proton e.MAS</span>
        <span class="brand__tag">Demo customer app</span>
      </div>
      <button
        v-if="persona"
        class="call-btn"
        :class="{ 'call-btn--live': !callIdle }"
        type="button"
        :aria-label="callIdle ? 'Call Proton support' : 'Hang up'"
        @click="callIdle ? startCall() : endCall()"
      >
        {{ callIdle ? '📞 Call' : callStatus === 'connecting' ? '… Hang up' : '⛔ Hang up' }}
      </button>
    </header>

    <label class="persona">
      <span>Signed in as</span>
      <select v-model="personaId">
        <option value="" disabled>Choose a demo customer…</option>
        <option v-for="p in personas" :key="p.id" :value="p.id">{{ p.label }}</option>
      </select>
    </label>
    <p v-if="persona" class="persona__hint">
      {{ persona.description }}<br />
      <strong>Try:</strong> {{ persona.scriptHint }}
    </p>
    <p v-if="callStatus === 'in-call'" class="banner banner--call">On a call with Proton AI as {{ persona?.label }}</p>
    <p v-if="callStatus === 'error'" class="banner banner--err" role="alert">
      Couldn’t connect the call. Check the microphone permission and try again.
    </p>

    <section ref="threadEl" class="thread" aria-live="polite">
      <p v-if="!persona" class="empty">Pick a demo customer to start chatting.</p>
      <p v-else-if="!messages.length" class="empty">
        No open conversation yet. Say hello, send a photo of a dashboard light or a voice note.
      </p>
      <div
        v-for="m in messages"
        :key="m.id"
        class="bubble"
        :class="m.from === 'customer' ? 'bubble--me' : 'bubble--them'"
      >
        <span v-if="m.from === 'proton'" class="bubble__who">{{ m.by === 'agent' ? 'Proton agent' : 'Proton AI' }}</span>
        <template v-for="(a, i) in m.attachments" :key="i">
          <img v-if="a.type === 'image' && a.url" class="media" :src="a.thumb || a.url" alt="Photo" />
          <video v-else-if="a.type === 'video' && a.url" class="media" :src="a.url" controls />
          <audio v-else-if="a.type === 'audio' && a.url" class="audio" :src="a.url" controls />
          <a v-else-if="a.url" :href="a.url" target="_blank" rel="noopener">Attachment</a>
        </template>
        <span v-if="m.text" class="bubble__text">{{ m.text }}</span>
        <span class="bubble__time">{{ timeLabel(m.at) }}</span>
      </div>
    </section>

    <p v-if="error" class="banner banner--err" role="alert">{{ error }}</p>

    <form class="composer" @submit.prevent="send()">
      <input
        ref="fileInput"
        class="hidden"
        type="file"
        accept="image/*,video/*"
        @change="onFile"
      />
      <button type="button" class="icon" :disabled="!persona || sending" aria-label="Send a photo or video" @click="pickFile">
        📎
      </button>
      <button
        type="button"
        class="icon"
        :class="{ 'icon--rec': voice.isActive.value }"
        :disabled="!persona || sending"
        :aria-label="voice.isActive.value ? 'Stop and send the voice note' : 'Record a voice note'"
        @click="toggleVoiceNote"
      >
        {{ voice.isActive.value ? '⏹' : '🎤' }}
      </button>
      <input
        v-model="draft"
        class="text"
        type="text"
        :placeholder="persona ? 'Message Proton' : 'Pick a customer first'"
        :disabled="!persona || voice.isActive.value"
      />
      <button type="submit" class="send" :disabled="!persona || sending || !draft.trim()">Send</button>
    </form>
  </div>
</template>

<style scoped>
.device {
  display: flex;
  flex-direction: column;
  gap: var(--space-sm);
  width: 100%;
  max-width: 400px;
  height: min(760px, calc(100vh - 140px));
  min-height: 520px;
  margin: 0 auto;
  padding: var(--space-md);
  border: 1px solid var(--border);
  border-radius: 1.75rem;
  background: var(--surface);
  color: var(--text);
}
.app-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-sm);
}
.brand {
  display: flex;
  flex-direction: column;
}
.brand__name {
  font-weight: 700;
  letter-spacing: -0.01em;
}
.brand__tag {
  font-size: 0.72rem;
  color: var(--text-muted);
}
.call-btn {
  border: none;
  border-radius: var(--radius-full);
  padding: 0.4rem 0.8rem;
  font-size: 0.82rem;
  font-weight: 650;
  background: var(--success);
  color: var(--bg);
}
.call-btn--live {
  background: var(--danger);
}
.persona {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  font-size: 0.75rem;
  color: var(--text-muted);
}
.persona select,
.text {
  padding: 0.45rem 0.55rem;
  font-size: 0.88rem;
  color: var(--text);
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  min-width: 0;
}
.persona__hint {
  margin: 0;
  font-size: 0.74rem;
  line-height: 1.35;
  color: var(--text-muted);
}
.thread {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 0.45rem;
  padding: var(--space-sm);
  background: var(--bg);
  border-radius: var(--radius-lg);
}
.empty {
  margin: auto;
  text-align: center;
  font-size: 0.82rem;
  color: var(--text-muted);
}
.bubble {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  max-width: 82%;
  padding: 0.45rem 0.65rem;
  border-radius: 1rem;
  font-size: 0.88rem;
  line-height: 1.4;
  overflow-wrap: anywhere;
}
.bubble--me {
  align-self: flex-end;
  background: var(--accent);
  color: var(--bg);
  border-bottom-right-radius: 0.3rem;
}
.bubble--them {
  align-self: flex-start;
  background: var(--surface-elevated);
  border-bottom-left-radius: 0.3rem;
}
.bubble__who {
  font-size: 0.7rem;
  font-weight: 650;
  color: var(--assistant);
}
.bubble__text {
  white-space: pre-wrap;
}
.bubble__time {
  align-self: flex-end;
  font-size: 0.65rem;
  opacity: 0.7;
}
.media {
  max-width: 100%;
  border-radius: 0.6rem;
}
.audio {
  width: 220px;
  max-width: 100%;
}
.banner {
  margin: 0;
  padding: 0.35rem 0.6rem;
  border-radius: var(--radius-md);
  font-size: 0.78rem;
}
.banner--err {
  color: var(--danger);
  border: 1px solid var(--danger);
}
.banner--call {
  color: var(--success);
  border: 1px solid var(--success);
}
.composer {
  display: flex;
  align-items: center;
  gap: 0.35rem;
}
.text {
  flex: 1;
}
.icon,
.send {
  border: 1px solid var(--border);
  border-radius: var(--radius-full);
  background: transparent;
  color: var(--text);
  padding: 0.4rem 0.55rem;
  font-size: 0.9rem;
}
.icon--rec {
  border-color: var(--danger);
  color: var(--danger);
}
.send {
  background: var(--accent);
  color: var(--bg);
  border-color: var(--accent);
  font-weight: 650;
  padding: 0.4rem 0.8rem;
}
button:disabled {
  opacity: 0.45;
}
.hidden {
  display: none;
}
</style>
