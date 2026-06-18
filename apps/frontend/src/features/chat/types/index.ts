export interface ChatTurnResponse {
  reply: string | null;
  language: string | null;
  sentiment: string | null;
  handoff: HandoffPayload | null;
}

export interface HandoffPayload {
  reason: string;
  language: string;
  summary: string | null;
  urgency: 'low' | 'medium' | 'high';
}

export interface ChatMessage {
  role: 'user' | 'assistant' | 'system';
  text: string;
  meta?: string;
}
