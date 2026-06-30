<!-- apps/frontend/src/features/dashboard/components/VolumeChart.vue -->
<script setup lang="ts">
import { computed } from 'vue';
import BaseChart from './BaseChart.vue';
import { themed } from './chartTheme';
import type { VolumeRow } from '@/features/dashboard/types';

const props = defineProps<{ rows: VolumeRow[] }>();

const months = computed(() => [...new Set(props.rows.map((r) => r.month))].sort());
const channels = computed(() => [...new Set(props.rows.map((r) => r.channel))].sort());

const series = computed(() =>
  channels.value.map((ch) => ({
    name: ch,
    data: months.value.map(
      (m) => props.rows.find((r) => r.month === m && r.channel === ch)?.volume ?? 0,
    ),
  })),
);

const options = computed(() =>
  themed({
    chart: { type: 'bar', stacked: true, toolbar: { show: false } },
    xaxis: { categories: months.value },
    legend: { position: 'top' as const },
    dataLabels: { enabled: false },
  }),
);
</script>

<template>
  <BaseChart title="Monthly Volume by Channel" :empty="rows.length === 0">
    <apexchart type="bar" height="300" :options="options" :series="series" />
  </BaseChart>
</template>
