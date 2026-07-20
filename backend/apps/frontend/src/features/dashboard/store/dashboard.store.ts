import { defineStore } from 'pinia';
import { computed, ref } from 'vue';
import { fetchDashboard } from '@/features/dashboard/api/dashboard.api';
import type { DashboardMetrics } from '@/features/dashboard/types';

const EMPTY: DashboardMetrics = {
  volume: [], resolution: [], csat: [], nps: [],
  speed: [], fallback: [], bounce: [], quality: [],
};

export const useDashboardStore = defineStore('dashboard', () => {
  const metrics = ref<DashboardMetrics>(EMPTY);
  const loading = ref(false);
  const error = ref<string | null>(null);
  const selectedChannel = ref<string>('all');

  // Union of channels across blocks, for the filter dropdown.
  const channels = computed<string[]>(() => {
    const set = new Set<string>();
    metrics.value.resolution.forEach((r) => set.add(r.channel));
    metrics.value.volume.forEach((r) => set.add(r.channel));
    return ['all', ...Array.from(set).sort()];
  });

  function pick<T extends { channel: string }>(rows: T[]): T[] {
    return selectedChannel.value === 'all'
      ? rows
      : rows.filter((r) => r.channel === selectedChannel.value);
  }

  const filtered = computed<DashboardMetrics>(() => ({
    volume: pick(metrics.value.volume),
    resolution: pick(metrics.value.resolution),
    csat: pick(metrics.value.csat),
    nps: pick(metrics.value.nps),
    speed: pick(metrics.value.speed),
    fallback: pick(metrics.value.fallback),
    bounce: pick(metrics.value.bounce),
    quality: pick(metrics.value.quality),
  }));

  async function load(): Promise<void> {
    loading.value = true;
    error.value = null;
    try {
      metrics.value = await fetchDashboard();
    } catch (e) {
      error.value = e instanceof Error ? e.message : 'Failed to load metrics';
    } finally {
      loading.value = false;
    }
  }

  return { metrics, loading, error, selectedChannel, channels, filtered, load };
});
