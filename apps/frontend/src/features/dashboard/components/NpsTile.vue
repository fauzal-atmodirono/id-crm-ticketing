<!-- apps/frontend/src/features/dashboard/components/NpsTile.vue -->
<script setup lang="ts">
import { computed } from 'vue';
import BaseChart from './BaseChart.vue';
import type { NpsRow } from '@/features/dashboard/types';

const props = defineProps<{ rows: NpsRow[] }>();

const promoters = computed(() => props.rows.reduce((s, r) => s + r.promoters, 0));
const passives = computed(() => props.rows.reduce((s, r) => s + r.passives, 0));
const detractors = computed(() => props.rows.reduce((s, r) => s + r.detractors, 0));
const respondents = computed(() => promoters.value + passives.value + detractors.value);
const nps = computed(() =>
  respondents.value === 0
    ? null
    : Math.round(((promoters.value - detractors.value) / respondents.value) * 100),
);
const series = computed(() => [promoters.value, passives.value, detractors.value]);
const options = computed(() => ({
  chart: { type: 'donut' },
  labels: ['Promoters', 'Passives', 'Detractors'],
  colors: ['#16a34a', '#eab308', '#dc2626'],
  legend: { position: 'bottom' as const },
}));
</script>

<template>
  <BaseChart title="NPS" :empty="respondents === 0">
    <p class="score">{{ nps }}</p>
    <apexchart type="donut" height="240" :options="options" :series="series" />
  </BaseChart>
</template>

<style scoped>
.score { font-size: 1.8rem; font-weight: 700; margin: 0 0 var(--space-sm); }
</style>
