import { API_BASE_URL } from '@/plugins/api';
import type { DashboardMetrics } from '@/features/dashboard/types';

export async function fetchDashboard(): Promise<DashboardMetrics> {
  const res = await fetch(`${API_BASE_URL}/metrics/dashboard`);
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`metrics/dashboard ${res.status}: ${body}`);
  }
  return (await res.json()) as DashboardMetrics;
}
