import { API_BASE_URL } from '@/plugins/api';
import type { VoiceTurnResult } from '@/features/voice/types';

export async function postVoiceTurn(sessionId: string, audio: Blob): Promise<VoiceTurnResult> {
  const form = new FormData();
  form.append('session_id', sessionId);
  // Backend reads UploadFile.content_type — preserve the recorder's MIME
  form.append('audio', audio, audio.type.includes('ogg') ? 'clip.ogg' : 'clip.webm');

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
