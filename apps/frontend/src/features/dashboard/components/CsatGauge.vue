<!-- apps/frontend/src/features/dashboard/components/CsatGauge.vue -->
<script setup lang="ts">
import { computed } from 'vue';
import BaseChart from './BaseChart.vue';
import type { CsatRow } from '@/features/dashboard/types';

const props = defineProps<{ rows: CsatRow[] }>();

const scored = computed(() => props.rows.filter((r) => r.avg_score !== null));
// CSAT is 1-5; show as a percentage of 5 for the radial gauge.
const avg = computed(() => {
  if (scored.value.length === 0) return 0;
  const mean =
    scored.value.reduce((s, r) => s + (r.avg_score ?? 0), 0) / scored.value.length;
  return Math.round((mean / 5) * 100);
});
const series = computed(() => [avg.value]);
const options = computed(() => ({
  chart: { type: 'radialBar' },
  plotOptions: {
    radialBar: { dataLabels: { value: { formatter: () => `${avg.value}%` } } },
  },
  labels: ['CSAT'],
}));
</script>

<template>
  <BaseChart title="CSAT" :empty="scored.length === 0">
    <apexchart type="radialBar" height="260" :options="options" :series="series" />
  </BaseChart>
</template>
