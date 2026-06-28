import { API_BASE_URL } from '@/plugins/api'

export interface PhoneToken {
  token: string
  identity: string
}

export async function fetchPhoneToken(): Promise<PhoneToken> {
  const res = await fetch(`${API_BASE_URL}/voice/phone/token`, { method: 'POST' })
  if (!res.ok) throw new Error(`token request failed: ${res.status}`)
  return (await res.json()) as PhoneToken
}
