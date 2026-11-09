import { API_BASE_URL } from '@/plugins/api'

// Backend: features/customer_app (DEMO_CUSTOMER_APP_ENABLED). The page sends
// a persona ID, never a number; the server maps it to the persona's fake
// number and refuses anything not on its allow-list.

export interface CustomerAttachment {
  type: string | null
  url: string | null
  thumb: string | null
}

export interface CustomerMessage {
  id: number
  from: 'customer' | 'proton'
  by: 'customer' | 'ai' | 'agent'
  text: string
  at: number | null
  attachments: CustomerAttachment[]
}

export interface CustomerThread {
  conversation_id: number | null
  messages: CustomerMessage[]
}

async function failure(res: Response): Promise<Error> {
  let detail = `${res.status}`
  try {
    const body = (await res.json()) as { detail?: string }
    if (body.detail) detail = `${res.status}: ${body.detail}`
  } catch {
    /* not JSON */
  }
  return new Error(detail)
}

export async function fetchThread(personaId: string): Promise<CustomerThread> {
  const url = `${API_BASE_URL}/customer-app/messages?persona=${encodeURIComponent(personaId)}`
  const res = await fetch(url)
  if (!res.ok) throw await failure(res)
  return (await res.json()) as CustomerThread
}

export async function sendMessage(
  personaId: string,
  text: string,
  file?: Blob,
  filename?: string,
): Promise<{ conversation_id: number }> {
  const form = new FormData()
  form.append('persona', personaId)
  form.append('text', text)
  if (file) form.append('file', file, filename ?? 'file')
  const res = await fetch(`${API_BASE_URL}/customer-app/messages`, { method: 'POST', body: form })
  if (!res.ok) throw await failure(res)
  return (await res.json()) as { conversation_id: number }
}
