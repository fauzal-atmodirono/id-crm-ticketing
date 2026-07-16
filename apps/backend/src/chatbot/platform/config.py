from __future__ import annotations

from functools import cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, loaded from environment variables and .env file."""

    # Provider configuration
    crm_provider: Literal["chatwoot", "zendesk"] = "chatwoot"
    voice_provider: Literal["mock", "gcp"] = "mock"
    knowledge_provider: Literal["mock", "zendesk", "vertex_search"] = "mock"
    session_store: Literal["memory", "firestore"] = "memory"

    # Server settings
    port: int = 8000
    host: str = "127.0.0.1"
    debug: bool = True

    # GCP / Vertex AI settings
    google_genai_use_vertexai: bool = False
    vertex_project_id: str | None = None
    vertex_location: str = "us-central1"
    gemini_model: str = "gemini-2.5-flash"

    # Vertex AI Search settings
    vertex_search_project_id: str = ""
    vertex_search_location: str = "global"
    vertex_search_data_store_id: str = "proton-kb"
    vertex_search_engine_id: str = "proton-kb-engine"

    # Zendesk credentials
    zendesk_subdomain: str = "your-zendesk-subdomain"
    zendesk_email: str = ""
    zendesk_app_id: str = ""
    zendesk_key_id: str = ""
    zendesk_secret_key: str = ""
    zendesk_api_token: str = ""

    # Per-customer identity for tickets on a SHARED Zendesk instance. The
    # domain is used to synthesize the requester email for session-scoped
    # pseudo-users; the name prefix labels the auto-created end-user; the tag
    # (when set) is applied to every ticket so Zendesk triggers can route
    # webhooks back to the correct backend. Override all three per fork so
    # Proton and Wah Chan tickets/end-users stay distinguishable in one tenant.
    zendesk_requester_domain: str = "proton.devoteam.example"
    zendesk_customer_name_prefix: str = "Proton AI Customer"
    zendesk_ticket_tag: str = ""

    # Sunshine Conversations webhook integration secret (used to HMAC-verify
    # inbound /webhooks/sunshine requests). Configured in the Sunshine
    # Conversations dashboard alongside the webhook URL.
    sunshine_webhook_secret: str = ""

    # Secret used to verify webhook calls from Zendesk Support (for standard ticket comment syncing)
    zendesk_support_webhook_secret: str = ""

    # Secret used to verify webhook calls for SLA escalation notifications
    sla_webhook_secret: str = ""

    # --- SLA-timer escalation engine (Chatwoot has no native SLA engine) ---
    # Master switch: when False (default) the in-app SLA scan scheduler is NOT
    # started, so nothing runs unless a deployment explicitly opts in.
    sla_engine_enabled: bool = False
    # First-response SLA: an OPEN conversation with no agent reply older than this
    # many hours fires an SLA_BREACH_NO_RESPONSE audit transition.
    sla_response_hours: int = 8
    # Resolution SLA: a non-resolved conversation older than this many hours fires
    # an SLA_BREACH_UNRESOLVED transition. A per-conversation sla_<int> label /
    # custom attribute (minutes) overrides this default when present.
    sla_resolution_hours: int = 48
    # How often (minutes) the SLA scan job scans Chatwoot conversations.
    sla_scan_interval_minutes: int = 15
    # Optional PIC WhatsApp number (E.164, e.g. "+60123456789") alerted via Twilio
    # on each breach. Empty (default) records the audit transition only, no alert.
    sla_pic_whatsapp: str = ""

    # Email channel — when True, AI replies become private draft notes instead of
    # public replies (draft-assist mode). Default False = auto-reply (public).
    email_draft_assist: bool = False

    # Firestore — persistent backing store for the handoff bridge.
    # When `handoff_store=firestore`, the bridge's session ↔ conversation
    # mapping survives backend restarts. Auth via ADC.
    handoff_store: Literal["memory", "firestore"] = "memory"
    firestore_project_id: str = "lv-playground-genai"
    firestore_database_id: str = "proton-db"
    firestore_handoff_collection: str = "handoff_sessions"
    firestore_audit_collection: str = "case_audit_log"

    # Bot-metrics dashboard (Zendesk -> BigQuery sync)
    bigquery_project_id: str = "lv-playground-genai"
    bigquery_dataset: str = "demo_proton"
    bigquery_conversations_table: str = "conversations"

    # Bot-metrics Phase 2 (per-turn MetricsPort -> BigQuery streaming)
    metrics_provider: Literal["noop", "bigquery"] = "noop"
    bigquery_turn_events_table: str = "turn_events"

    # Bot-metrics Phase 2 (in-app Zendesk -> BQ sync scheduler / the "trigger")
    metrics_sync_enabled: bool = False
    metrics_sync_interval_hours: int = 6

    # Bot-metrics Phase 4 (manual QA accuracy/quality entry)
    qa_provider: Literal["noop", "bigquery"] = "noop"
    bigquery_qa_labels_table: str = "qa_labels"
    qa_api_key: str = ""

    # Bot-metrics Phase 5 (FAQ feedback recording)
    bigquery_faq_feedback_table: str = "faq_feedback"

    # Bot-metrics export: SMTP settings for emailing scheduled reports
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    # Comma-separated list of recipient email addresses
    report_recipients: str = ""
    # Set to True to enable scheduled report emails (disabled by default)
    report_enabled: bool = False
    report_interval_hours: int = 24

    # Bot-metrics anomaly detection thresholds
    # Z-score multiplier for channel-volume anomaly detection
    anomaly_zscore_k: float = 3.0
    # Minimum baseline mean volume before a channel can be flagged (low-traffic guard)
    anomaly_min_baseline: int = 20

    def report_recipient_list(self) -> list[str]:
        return [r.strip() for r in self.report_recipients.split(",") if r.strip()]

    # Chatwoot settings
    chatwoot_api_url: str = "http://localhost:3000"
    chatwoot_api_token: str = ""
    chatwoot_account_id: int = 1
    chatwoot_enabled: bool = True
    # API-channel inbox that our backend creates conversations in and receives
    # agent-reply webhooks from. 0 = unset (fail fast in the adapter).
    chatwoot_inbox_id: int = 0
    # Team the escalated conversation is assigned to (the native-inbox handoff).
    chatwoot_agent_team_id: int = 0
    # Label applied to escalated conversations so agents can filter them.
    chatwoot_escalation_label: str = "ai-escalation"
    # Shared secret required on inbound /webhooks/chatwoot calls (Chatwoot has no
    # built-in HMAC). Compared constant-time; empty leaves the endpoint open.
    chatwoot_webhook_secret: str = ""
    # Domain used to synthesize a customer email on Chatwoot contacts. Web/WhatsApp
    # customers have no real email, but downstream ticketing that keys customers by
    # email (e.g. a Chatwoot->Zammad sync) needs one. Deterministic per session:
    # <sanitized session_id>@<domain>. Use a real TLD so email-format validation passes.
    chatwoot_customer_email_domain: str = "proton-demo.my"
    # Whether an INCOMING message in the Chatwoot inbox runs the AI. Only true when
    # Chatwoot itself is the customer channel (website widget). For a handoff-console
    # deployment (customers on WhatsApp/web, Chatwoot only for agents) this MUST stay
    # False, or the escalation seed message / forwarded customer messages trigger the
    # bot and it re-escalates in an infinite loop.
    chatwoot_bot_replies_to_incoming: bool = False
    # EMAIL-type Chatwoot inbox. When an email arrives, Chatwoot opens a
    # conversation in this inbox and fires a message_created webhook; we run the
    # AI and post a PUBLIC reply so Chatwoot emails it back to the customer. This
    # is the Chatwoot equivalent of the (Zendesk-native) email channel and is the
    # ONLY inbox on which incoming messages take the email path — the API-channel
    # inbox (chatwoot_inbox_id) still uses the native-handoff branch.
    # 0 = email-on-chatwoot disabled (no email routing). Set to the numeric id of
    # the provisioned email inbox to enable it. Honours email_draft_assist just
    # like the Zendesk email path (True = private draft note instead of a send).
    chatwoot_email_inbox_id: int = 0

    # Zammad settings
    zammad_api_url: str = "http://localhost:3000"
    zammad_api_token: str = ""
    zammad_enabled: bool = True

    # Twilio (WhatsApp Phase A; Phone Phase C). Empty by default so dev
    # environments without Twilio credentials still boot.
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_number: str = ""  # e.g. "whatsapp:+60123456789"
    twilio_phone_number: str = ""  # e.g. "+60123456789"
    twilio_webhook_base_url: str = ""  # public https base for webhooks (ngrok in dev)

    # Voice settings
    voice_default_lang: str = "en-US"
    gemini_tts_model: str = "gemini-2.5-flash-tts"
    gemini_tts_voice: str = "Kore"

    # Phone (real-time Gemini Live) settings. Vertex publisher model id — the
    # AI-Studio name "gemini-live-2.5-flash-preview" is rejected by Vertex (1008).
    gemini_live_model: str = "gemini-live-2.5-flash-native-audio"
    gemini_live_voice: str = "Kore"
    # Optional output language hint (e.g. "ms-MY" for Bahasa Melayu). Empty = let
    # the model auto-detect. Only honored by half-cascade Live models; native-audio
    # models auto-detect language and ignore it.
    gemini_live_language: str = ""
    # Browser-softphone access tokens (Twilio Voice grant)
    twilio_api_key_sid: str = ""
    twilio_api_key_secret: str = ""
    twilio_twiml_app_sid: str = ""
    # Public wss base for the <Stream> URL; falls back to twilio_webhook_base_url with https->wss
    public_wss_base_url: str = ""
    # Browser-softphone token endpoint hardening (a public SPA calls it, so it is
    # unauthenticated by nature). Short TTL limits a leaked token's lifetime; the
    # per-IP rate limit bounds the billing blast radius if the endpoint is abused.
    phone_token_ttl_seconds: int = 300
    phone_token_rate_limit: int = 10
    phone_token_rate_window_seconds: int = 60

    # Frontend CORS — origins of the Vue dev/prod app (comma-separated in env).
    # Defaults cover Vite's first few fallback ports (5173-5180) so a stale dev
    # server on 5173 doesn't break a fresh one bound to 5174+.
    frontend_origins: list[str] = [
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:5175",
        "http://localhost:5176",
        "http://localhost:5177",
        "http://localhost:5178",
        "http://localhost:5179",
        "http://localhost:5180",
    ]

    # Settings configurations
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@cache
def get_settings() -> Settings:
    """Helper function to load and cache application settings."""
    return Settings()
