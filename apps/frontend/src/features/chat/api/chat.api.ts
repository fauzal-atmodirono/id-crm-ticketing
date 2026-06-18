import { API_BASE_URL } from '@/plugins/api';
import type { ChatTurnResponse } from '@/features/chat/types';

export async function postChatTurn(sessionId: string, text: string): Promise<ChatTurnResponse> {
  const res = await fetch(`${API_BASE_URL}/chat/turn`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, text }),
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`chat/turn ${res.status}: ${body}`);
  }
  return (await res.json()) as ChatTurnResponse;
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
