import { API_BASE_URL } from '@/plugins/api'

// Backend /demo/* (features/demo/router.py). Only registered when the tenant
// sets DEMO_CONSOLE_ENABLED=true; on the VM it is reachable only through the
// test console's basic-auth Caddy block.

export interface DemoLink {
  id: number
  url: string
}

export interface DemoPersonaStatus {
  id: string
  label: string
  number: string
  language: string
  description: string
  scriptHint: string
  shows: string[]
  seedable: boolean
  seeded: boolean
  contact: (DemoLink & { name: string | null }) | null
  conversation_count: number
  latest_conversation: (DemoLink & { status: string | null }) | null
  customer360_url: string | null
  rsa_incidents: number | null
  warning: string | null
}

export interface DemoStatus {
  target: { account_id: number; inbox_id: number }
  inbox: { channel_type: string | null; ok: boolean }
  rsa_configured: boolean
  personas: DemoPersonaStatus[]
}

export interface DemoSeedReport {
  created_contacts: string[]
  updated_contacts: string[]
  created_conversations: number
  skipped_history: string[]
  rsa_created: number
  conflicts: { persona: string; contact_id: number; reason: string }[]
  errors: string[]
}

export interface DemoResetReport {
  removed: { contacts: number; conversations: number; rsa_incidents: number }
  errors: string[]
  not_demo_records: { persona: string; contact_id: number }[]
  seed: DemoSeedReport
}

async function call<T>(path: string, method: 'GET' | 'POST'): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    method,
    // The backend refuses seed/reset without this header (CSRF guard).
    headers: method === 'POST' ? { 'X-Demo-Console': '1' } : {},
  })
  if (!res.ok) {
    let detail = `${res.status}`
    try {
      const body = (await res.json()) as { detail?: string }
      if (body.detail) detail = `${res.status}: ${body.detail}`
    } catch {
      /* not JSON */
    }
    if (res.status === 404) detail = '404: demo console is off (DEMO_CONSOLE_ENABLED)'
    throw new Error(detail)
  }
  return (await res.json()) as T
}

export const fetchDemoStatus = (): Promise<DemoStatus> => call('/demo/personas', 'GET')
export const seedDemo = (): Promise<DemoSeedReport> => call('/demo/seed', 'POST')
export const resetDemo = (): Promise<DemoResetReport> => call('/demo/reset', 'POST')
