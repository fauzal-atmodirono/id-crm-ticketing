<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue';

const props = defineProps<{
  analyser: AnalyserNode | null;
  active: boolean;
  hot: boolean;
}>();

const canvasEl = ref<HTMLCanvasElement | null>(null);
let rafHandle: number | null = null;
let timeBuf: Uint8Array | null = null;

function draw(): void {
  const canvas = canvasEl.value;
  const analyser = props.analyser;
  if (!canvas || !analyser) {
    rafHandle = null;
    return;
  }

  const ctx = canvas.getContext('2d');
  if (!ctx) return;

  const dpr = window.devicePixelRatio || 1;
  const cssW = canvas.clientWidth;
  const cssH = canvas.clientHeight;
  if (canvas.width !== cssW * dpr || canvas.height !== cssH * dpr) {
    canvas.width = cssW * dpr;
    canvas.height = cssH * dpr;
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssW, cssH);

  if (!timeBuf || timeBuf.length !== analyser.fftSize) {
    timeBuf = new Uint8Array(analyser.fftSize);
  }
  analyser.getByteTimeDomainData(timeBuf);

  // Down-sample to N bars for a stylized voice-bar visualization.
  const bars = 48;
  const step = Math.floor(timeBuf.length / bars);
  const midY = cssH / 2;
  const barWidth = (cssW / bars) * 0.55;
  const barGap = (cssW / bars) * 0.45;
  const fill = props.hot ? '#22d3ee' : '#64748b';
  ctx.fillStyle = fill;

  const buf = timeBuf;
  for (let i = 0; i < bars; i++) {
    let peak = 0;
    for (let j = 0; j < step; j++) {
      const v = Math.abs((buf[i * step + j] ?? 128) - 128) / 128;
      if (v > peak) peak = v;
    }
    // Light shaping + min height so silent bars are still visible.
    const amp = Math.max(0.04, Math.pow(peak, 0.85));
    const barH = Math.max(2, amp * cssH * 0.9);
    const x = i * (barWidth + barGap);
    ctx.fillRect(x, midY - barH / 2, barWidth, barH);
  }

  rafHandle = requestAnimationFrame(draw);
}

function startLoop(): void {
  if (rafHandle != null) return;
  rafHandle = requestAnimationFrame(draw);
}

function stopLoop(): void {
  if (rafHandle != null) {
    cancelAnimationFrame(rafHandle);
    rafHandle = null;
  }
  const canvas = canvasEl.value;
  if (canvas) {
    const ctx = canvas.getContext('2d');
    if (ctx) ctx.clearRect(0, 0, canvas.width, canvas.height);
  }
}

watch(
  () => props.active && props.analyser != null,
  (on) => (on ? startLoop() : stopLoop()),
  { immediate: true },
);

onBeforeUnmount(stopLoop);
</script>

<template>
  <div class="wave" :class="{ active, hot }">
    <canvas ref="canvasEl" />
  </div>
</template>

<style scoped>
.wave {
  width: 100%;
  height: 72px;
  border-radius: var(--radius-md);
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.02), rgba(255, 255, 255, 0.04));
  border: 1px solid var(--border);
  padding: 0.5rem 0.75rem;
  transition: border-color 200ms ease, background 200ms ease;
}
.wave.active {
  border-color: rgba(34, 211, 238, 0.45);
  background: linear-gradient(180deg, rgba(34, 211, 238, 0.06), rgba(34, 211, 238, 0.02));
}
.wave.hot {
  border-color: rgba(34, 211, 238, 0.9);
  box-shadow: 0 0 0 2px rgba(34, 211, 238, 0.18);
}
canvas {
  width: 100%;
  height: 100%;
  display: block;
}
</style>
