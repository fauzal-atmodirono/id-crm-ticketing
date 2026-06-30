<!-- apps/frontend/src/features/dashboard/components/RateChart.vue -->
<script setup lang="ts">
import { computed } from 'vue';
import BaseChart from './BaseChart.vue';
import { themed } from './chartTheme';

const props = defineProps<{
  title: string;
  rows: { channel: string }[];
  rate: (row: { channel: string }) => number | null;
}>();

const series = computed(() => [
  {
    name: props.title,
    data: props.rows.map((r) => Math.round((props.rate(r) ?? 0) * 100)),
  },
]);
const options = computed(() =>
  themed({
    chart: { type: 'bar', toolbar: { show: false } },
    xaxis: { categories: props.rows.map((r) => r.channel) },
    yaxis: { max: 100, title: { text: '%' } },
    dataLabels: { enabled: true, formatter: (v: number) => `${v}%` },
  }),
);
</script>

<template>
  <BaseChart :title="title" :empty="rows.length === 0">
    <apexchart type="bar" height="240" :options="options" :series="series" />
  </BaseChart>
</template>
