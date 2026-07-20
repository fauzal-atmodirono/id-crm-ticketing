<!-- apps/frontend/src/views/DashboardView.vue -->
<script setup lang="ts">
import { computed, onMounted } from 'vue';
import { useDashboardStore } from '@/features/dashboard';
import ChannelFilter from '@/features/dashboard/components/ChannelFilter.vue';
import MetricCard from '@/features/dashboard/components/MetricCard.vue';
import VolumeChart from '@/features/dashboard/components/VolumeChart.vue';
import ResolutionChart from '@/features/dashboard/components/ResolutionChart.vue';
import CsatGauge from '@/features/dashboard/components/CsatGauge.vue';
import NpsTile from '@/features/dashboard/components/NpsTile.vue';
import SpeedChart from '@/features/dashboard/components/SpeedChart.vue';
import RateChart from '@/features/dashboard/components/RateChart.vue';
import QualityChart from '@/features/dashboard/components/QualityChart.vue';

const store = useDashboardStore();
onMounted(() => {
  if (store.metrics.volume.length === 0) store.load();
});

const m = computed(() => store.filtered);

const totalConvos = computed(() => m.value.resolution.reduce((s, r) => s + r.total, 0));
const botPct = computed(() => {
  const bot = m.value.resolution.reduce((s, r) => s + r.closed_by_bot, 0);
  return totalConvos.value === 0 ? '—' : `${Math.round((bot / totalConvos.value) * 100)}%`;
});
const csatAvg = computed(() => {
  const scored = m.value.csat.filter((r) => r.avg_score !== null);
  if (scored.length === 0) return '—';
  const mean = scored.reduce((s, r) => s + (r.avg_score ?? 0), 0) / scored.length;
  return mean.toFixed(1);
});
const npsScore = computed(() => {
  const p = m.value.nps.reduce((s, r) => s + r.promoters, 0);
  const d = m.value.nps.reduce((s, r) => s + r.detractors, 0);
  const resp = p + m.value.nps.reduce((s, r) => s + r.passives, 0) + d;
  return resp === 0 ? '—' : String(Math.round(((p - d) / resp) * 100));
});

// fallback/bounce share { channel } shape; expose rate accessors for RateChart.
const fallbackRate = (row: { channel: string }) =>
  m.value.fallback.find((f) => f.channel === row.channel)?.fallback_rate ?? null;
const bounceRate = (row: { channel: string }) =>
  m.value.bounce.find((b) => b.channel === row.channel)?.bounce_rate ?? null;
</script>

<template>
  <section class="dashboard">
    <header class="bar">
      <h2>Bot Metrics</h2>
      <div class="actions">
        <ChannelFilter v-model="store.selectedChannel" :channels="store.channels" />
        <button :disabled="store.loading" @click="store.load()">
          {{ store.loading ? 'Loading…' : 'Refresh' }}
        </button>
      </div>
    </header>

    <p v-if="store.error" class="error">{{ store.error }}</p>

    <div class="kpis">
      <MetricCard label="Total conversations" :value="String(totalConvos)" />
      <MetricCard label="Bot-resolved" :value="botPct" />
      <MetricCard label="CSAT (avg /5)" :value="csatAvg" />
      <MetricCard label="NPS" :value="npsScore" />
    </div>

    <VolumeChart :rows="m.volume" />

    <div class="grid-2">
      <ResolutionChart :rows="m.resolution" />
      <div class="stack">
        <CsatGauge :rows="m.csat" />
        <NpsTile :rows="m.nps" />
      </div>
    </div>

    <div class="grid-3">
      <SpeedChart :rows="m.speed" />
      <RateChart title="Fallback Rate" :rows="m.fallback" :rate="fallbackRate" />
      <RateChart title="Bounce Rate" :rows="m.bounce" :rate="bounceRate" />
    </div>

    <QualityChart :rows="m.quality" />
  </section>
</template>

<style scoped>
.dashboard {
  width: 100%;
  max-width: 1200px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: var(--space-md);
}
.bar { display: flex; justify-content: space-between; align-items: center; }
.bar h2 { margin: 0; font-size: 1.1rem; }
.actions { display: flex; gap: var(--space-sm); align-items: center; }
.actions button {
  background: var(--surface);
  border: 1px solid var(--border);
  color: var(--text);
  border-radius: var(--radius-full);
  padding: 0.3rem 0.9rem;
  cursor: pointer;
}
.error { color: var(--danger); margin: 0; }
.kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: var(--space-md); }
.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-md); }
.grid-3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: var(--space-md); }
.stack { display: flex; flex-direction: column; gap: var(--space-md); }
@media (max-width: 860px) {
  .kpis { grid-template-columns: repeat(2, 1fr); }
  .grid-2, .grid-3 { grid-template-columns: 1fr; }
}
</style>
