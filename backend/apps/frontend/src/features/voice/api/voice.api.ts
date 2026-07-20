import { API_BASE_URL } from '@/plugins/api';
import type { HandoffPayload } from '@/features/chat/types';
import type { VoiceTurnResult } from '@/features/voice/types';

export async function postVoiceTurn(sessionId: string, audio: Blob): Promise<VoiceTurnResult> {
  const form = new FormData();
  form.append('session_id', sessionId);
  // Gemini accepts audio/wav natively — the backend forwards UploadFile.content_type
  // straight into the ADK Part, so the blob type is what matters here.
  const filename = audio.type.includes('wav')
    ? 'clip.wav'
    : audio.type.includes('ogg')
      ? 'clip.ogg'
      : 'clip.webm';
  form.append('audio', audio, filename);

  const res = await fetch(`${API_BASE_URL}/voice/turn`, {
    method: 'POST',
    body: form,
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`voice/turn ${res.status}: ${body}`);
  }
  const audioBlob = await res.blob();
  const encodedReply = res.headers.get('X-Reply-Text') ?? '';
  const forwarded = res.headers.get('X-Forwarded-To-Agent') === '1';
  const encodedTranscription = res.headers.get('X-User-Transcription') ?? '';

  return {
    replyText: encodedReply ? decodeURIComponent(encodedReply) : '',
    handoff: parseHandoffHeaders(res.headers),
    audioBlob,
    forwardedToAgent: forwarded,
    userTranscription: encodedTranscription ? decodeURIComponent(encodedTranscription) : '',
  };
}

export async function postVoiceTts(text: string, language: string = 'en-US'): Promise<Blob> {
  const res = await fetch(`${API_BASE_URL}/voice/tts`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ text, language }),
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`voice/tts ${res.status}: ${body}`);
  }
  return await res.blob();
}

function parseHandoffHeaders(headers: Headers): HandoffPayload | null {
  const reason = headers.get('X-Handoff-Reason');
  if (!reason) return null;
  const encodedSummary = headers.get('X-Handoff-Summary');
  const urgency = headers.get('X-Handoff-Urgency') ?? 'medium';
  const language = headers.get('X-Handoff-Language') ?? 'en';
  const liveChat = headers.get('X-Handoff-Live-Chat') === '1';

  return {
    reason,
    language,
    summary: encodedSummary ? decodeURIComponent(encodedSummary) : null,
    urgency: (['low', 'medium', 'high'].includes(urgency) ? urgency : 'medium') as
      | 'low'
      | 'medium'
      | 'high',
    live_chat_available: liveChat,
  };
}
