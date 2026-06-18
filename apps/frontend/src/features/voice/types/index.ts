export interface VoiceTurnResult {
  replyText: string;
  handoffReason: string | null;
  audioBlob: Blob;
}

export interface VoiceEntry {
  kind: 'user' | 'assistant' | 'system';
  text: string;
  audioUrl?: string;
  meta?: string;
}
