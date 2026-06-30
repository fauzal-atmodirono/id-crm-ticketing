<!-- apps/frontend/src/features/dashboard/components/ResolutionChart.vue -->
<script setup lang="ts">
import { computed } from 'vue';
import BaseChart from './BaseChart.vue';
import { themed } from './chartTheme';
import type { ResolutionRow } from '@/features/dashboard/types';

const props = defineProps<{ rows: ResolutionRow[] }>();

const bot = computed(() => props.rows.reduce((s, r) => s + r.closed_by_bot, 0));
const agent = computed(() => props.rows.reduce((s, r) => s + r.transfer_to_agent, 0));
const series = computed(() => [bot.value, agent.value]);
const options = computed(() =>
  themed({
    chart: { type: 'donut' },
    labels: ['Closed by bot', 'Transferred to agent'],
    legend: { position: 'bottom' as const },
  }),
);
</script>

<template>
  <BaseChart title="Resolution Split" :empty="bot + agent === 0">
    <apexchart type="donut" height="280" :options="options" :series="series" />
  </BaseChart>
</template>
