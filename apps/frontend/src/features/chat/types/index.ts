export interface ChatTurnResponse {
  reply: string | null;
  language: string | null;
  sentiment: string | null;
  handoff: HandoffPayload | null;
  forwarded_to_agent: boolean;
}

export interface HandoffPayload {
  reason: string;
  language: string;
  summary: string | null;
  urgency: 'low' | 'medium' | 'high';
  live_chat_available: boolean;
}

export interface ChatMessage {
  role: 'user' | 'assistant' | 'system' | 'agent';
  text: string;
  meta?: string;
}

export interface AgentMessageEvent {
  type: 'agent_message';
  author_name: string;
  text: string;
  timestamp: string;
}
