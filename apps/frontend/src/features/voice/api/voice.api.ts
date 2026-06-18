import { API_BASE_URL } from '@/plugins/api';
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
  const handoffReason = res.headers.get('X-Handoff-Reason');
  return {
    replyText: encodedReply ? decodeURIComponent(encodedReply) : '',
    handoffReason,
    audioBlob,
  };
}
