<!-- apps/frontend/src/features/dashboard/components/SpeedChart.vue -->
<script setup lang="ts">
import { computed } from 'vue';
import BaseChart from './BaseChart.vue';
import { themed } from './chartTheme';
import type { SpeedRow } from '@/features/dashboard/types';

const props = defineProps<{ rows: SpeedRow[] }>();

const channels = computed(() => [...new Set(props.rows.map((r) => r.channel))].sort());
const pick = (ch: string, first: boolean) =>
  props.rows.find((r) => r.channel === ch && r.is_first_turn === first)?.p99_latency_ms ?? 0;
const series = computed(() => [
  { name: 'First turn', data: channels.value.map((c) => pick(c, true)) },
  { name: 'Follow-up', data: channels.value.map((c) => pick(c, false)) },
]);
const options = computed(() =>
  themed({
    chart: { type: 'bar', toolbar: { show: false } },
    xaxis: { categories: channels.value },
    yaxis: { title: { text: 'p99 latency (ms)' } },
    legend: { position: 'top' as const },
    dataLabels: { enabled: false },
  }),
);
</script>

<template>
  <BaseChart title="Speed of Response (p99)" :empty="rows.length === 0">
    <apexchart type="bar" height="260" :options="options" :series="series" />
  </BaseChart>
</template>
