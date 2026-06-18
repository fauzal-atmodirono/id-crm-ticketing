export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000';

export interface HealthCheck {
  status: string;
  crm_provider: string;
  voice_provider: string;
  model: string;
}

export async function fetchHealth(): Promise<HealthCheck> {
  const res = await fetch(`${API_BASE_URL}/`);
  if (!res.ok) throw new Error(`Health check failed: ${res.status}`);
  return (await res.json()) as HealthCheck;
}
