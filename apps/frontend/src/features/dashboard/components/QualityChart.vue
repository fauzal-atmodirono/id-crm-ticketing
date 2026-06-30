<!-- apps/frontend/src/features/dashboard/components/QualityChart.vue -->
<script setup lang="ts">
import { computed } from 'vue';
import BaseChart from './BaseChart.vue';
import type { QualityRow } from '@/features/dashboard/types';

const props = defineProps<{ rows: QualityRow[] }>();

const options = computed(() => ({
  chart: { type: 'bar', toolbar: { show: false } },
  xaxis: { categories: props.rows.map((r) => r.channel) },
  yaxis: { max: 100 },
  legend: { position: 'top' as const },
  dataLabels: { enabled: false },
}));
const series = computed(() => [
  { name: 'Accuracy', data: props.rows.map((r) => Math.round(r.avg_accuracy ?? 0)) },
  { name: 'Quality', data: props.rows.map((r) => Math.round(r.avg_quality ?? 0)) },
]);
</script>

<template>
  <BaseChart title="Accuracy & Quality by Channel" :empty="rows.length === 0">
    <apexchart type="bar" height="260" :options="options" :series="series" />
  </BaseChart>
</template>
