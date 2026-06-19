export interface ProductCard {
  title: string;
  description: string;
  image_url: string | null;
  price: string | null;
  url: string | null;
}

export interface ChatTurnResponse {
  reply: string | null;
  language: string | null;
  sentiment: string | null;
  handoff: HandoffPayload | null;
  forwarded_to_agent: boolean;
  products: ProductCard[];
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
  products?: ProductCard[];
}

export interface AgentMessageEvent {
  type: 'agent_message';
  author_name: string;
  text: string;
  timestamp: string;
}
