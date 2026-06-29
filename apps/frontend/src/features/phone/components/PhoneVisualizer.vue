<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'

const props = defineProps<{
  analyser: AnalyserNode | null
  active: boolean
}>()

const canvasEl = ref<HTMLCanvasElement | null>(null)
let raf: number | null = null
let freq: Uint8Array | null = null
let phase = 0

const reduceMotion =
  typeof window !== 'undefined' &&
  typeof window.matchMedia === 'function' &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches

const BARS = 28

function render(): void {
  const canvas = canvasEl.value
  const ctx = canvas?.getContext('2d')
  if (!canvas || !ctx) {
    raf = null
    return
  }

  const dpr = window.devicePixelRatio || 1
  const w = canvas.clientWidth
  const h = canvas.clientHeight
  if (canvas.width !== w * dpr || canvas.height !== h * dpr) {
    canvas.width = w * dpr
    canvas.height = h * dpr
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.clearRect(0, 0, w, h)

  const amps: number[] = []
  const analyser = props.analyser
  if (analyser) {
    if (!freq || freq.length !== analyser.frequencyBinCount) {
      freq = new Uint8Array(analyser.frequencyBinCount)
    }
    analyser.getByteFrequencyData(freq)
    const step = Math.max(1, Math.floor(freq.length / BARS))
    for (let i = 0; i < BARS; i++) {
      let sum = 0
      for (let j = 0; j < step; j++) sum += freq[i * step + j] ?? 0
      amps.push(sum / step / 255)
    }
  } else {
    // Ambient "breathing" fallback when no audio stream is available.
    phase += reduceMotion ? 0 : 0.06
    for (let i = 0; i < BARS; i++) {
      amps.push(reduceMotion ? 0.14 : 0.12 + 0.12 * (Math.sin(phase + i * 0.45) * 0.5 + 0.5))
    }
  }

  const gap = (w / BARS) * 0.42
  const barW = w / BARS - gap
  const mid = h / 2
  const grad = ctx.createLinearGradient(0, 0, 0, h)
  grad.addColorStop(0, '#38bdf8')
  grad.addColorStop(1, '#0ea5e9')
  ctx.fillStyle = grad

  for (let i = 0; i < BARS; i++) {
    const amp = Math.max(0.06, amps[i] ?? 0)
    const bh = Math.max(3, amp * h * 0.92)
    const x = i * (barW + gap) + gap / 2
    const y = mid - bh / 2
    const r = Math.min(barW / 2, 3)
    if (typeof ctx.roundRect === 'function') {
      ctx.beginPath()
      ctx.roundRect(x, y, barW, bh, r)
      ctx.fill()
    } else {
      ctx.fillRect(x, y, barW, bh)
    }
  }

  raf = requestAnimationFrame(render)
}

function start(): void {
  if (raf == null) raf = requestAnimationFrame(render)
}

function stop(): void {
  if (raf != null) {
    cancelAnimationFrame(raf)
    raf = null
  }
  const canvas = canvasEl.value
  const ctx = canvas?.getContext('2d')
  if (canvas && ctx) ctx.clearRect(0, 0, canvas.width, canvas.height)
}

watch(
  () => props.active,
  (active) => (active ? start() : stop()),
  { immediate: true },
)
onBeforeUnmount(stop)
</script>

<template>
  <canvas ref="canvasEl" class="phone-viz" aria-hidden="true"></canvas>
</template>

<style scoped>
.phone-viz {
  display: block;
  width: 100%;
  height: 56px;
}
</style>
