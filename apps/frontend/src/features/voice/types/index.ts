import type { HandoffPayload } from '@/features/chat/types';

export interface VoiceTurnResult {
  replyText: string;
  handoff: HandoffPayload | null;
  audioBlob: Blob;
}

export interface VoiceEntry {
  kind: 'user' | 'assistant' | 'system';
  text: string;
  audioUrl?: string;
  meta?: string;
}
