export interface VolumeRow { month: string; channel: string; volume: number; }
export interface ResolutionRow {
  channel: string;
  closed_by_bot: number;
  transfer_to_agent: number;
  total: number;
  closed_by_bot_pct: number | null;
  transfer_to_agent_pct: number | null;
}
export interface CsatRow {
  channel: string;
  respondents: number;
  avg_score: number | null;
  satisfied_rate: number | null;
}
export interface NpsRow {
  channel: string;
  respondents: number;
  promoters: number;
  passives: number;
  detractors: number;
  nps: number | null;
}
export interface SpeedRow {
  channel: string;
  is_first_turn: boolean;
  p99_latency_ms: number | null;
  avg_latency_ms: number | null;
  turns: number;
}
export interface FallbackRow { channel: string; fallback_rate: number | null; turns: number; }
export interface BounceRow {
  channel: string;
  bounced: number;
  total_sessions: number;
  bounce_rate: number | null;
}
export interface QualityRow {
  channel: string;
  labels: number;
  avg_accuracy: number | null;
  avg_quality: number | null;
}
export interface DashboardMetrics {
  volume: VolumeRow[];
  resolution: ResolutionRow[];
  csat: CsatRow[];
  nps: NpsRow[];
  speed: SpeedRow[];
  fallback: FallbackRow[];
  bounce: BounceRow[];
  quality: QualityRow[];
}
