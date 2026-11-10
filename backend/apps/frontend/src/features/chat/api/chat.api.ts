import { API_BASE_URL } from '@/plugins/api';
import type { ChatTurnResponse, CsatResponse } from '@/features/chat/types';
import { personaFields } from '@/features/phone/personas';

/** `personaPhone`: the test-console persona's number ('' = anonymous, sends nothing). */
export async function postChatTurn(
  sessionId: string,
  text: string,
  personaPhone = '',
): Promise<ChatTurnResponse> {
  const res = await fetch(`${API_BASE_URL}/chat/turn`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, text, ...personaFields(personaPhone) }),
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`chat/turn ${res.status}: ${body}`);
  }
  return (await res.json()) as ChatTurnResponse;
}

export async function postCsat(sessionId: string, score: number): Promise<CsatResponse> {
  const res = await fetch(`${API_BASE_URL}/chat/csat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, score }),
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`chat/csat ${res.status}: ${body}`);
  }
  return (await res.json()) as CsatResponse;
}

/**
 * Open a Server-Sent Events stream that delivers human-agent messages for a
 * handed-off session. The caller is responsible for closing the returned
 * EventSource (e.g. on session reset or component unmount).
 */
export function openAgentStream(sessionId: string): EventSource {
  const url = `${API_BASE_URL}/chat/stream/${encodeURIComponent(sessionId)}`;
  return new EventSource(url);
}
