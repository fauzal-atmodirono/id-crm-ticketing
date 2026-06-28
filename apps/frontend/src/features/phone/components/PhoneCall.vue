<script setup lang="ts">
import { ref, onBeforeUnmount } from 'vue'
import { Device, type Call } from '@twilio/voice-sdk'
import { fetchPhoneToken } from '../api/phone.api'

const status = ref<'idle' | 'connecting' | 'in-call' | 'error'>('idle')
let device: Device | null = null
let call: Call | null = null

async function startCall() {
  if (status.value !== 'idle' && status.value !== 'error') return
  try {
    status.value = 'connecting'
    const { token } = await fetchPhoneToken()
    device = new Device(token)
    call = await device.connect()
    call.on('disconnect', endCall)
    status.value = 'in-call'
  } catch {
    status.value = 'error'
  }
}

function endCall() {
  call?.disconnect()
  device?.destroy()
  call = null
  device = null
  status.value = 'idle'
}

onBeforeUnmount(endCall)
</script>

<template>
  <div class="phone-call">
    <button v-if="status === 'idle' || status === 'error'" @click="startCall">📞 Call support</button>
    <button v-else-if="status === 'in-call'" @click="endCall">⛔ Hang up</button>
    <span v-else>Connecting…</span>
    <span v-if="status === 'error'" class="err">Call failed</span>
  </div>
</template>

<style scoped>
.phone-call {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
.err {
  color: #c0392b;
  font-size: 0.875rem;
}
</style>
