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

    # --- Real-time, CRM-editable FAQ knowledge base (semantic matching) ---
    # The CRM team authors FAQ entries via the admin router; each write embeds
    # the entry (question + answer) with `embedding_model` and stores the vector
    # in `live_faq_collection` (Firestore, same DB as the handoff store). Auto-
    # created on first write — no manual provisioning or vector index needed,
    # since `/kb/suggest` ranks entries with in-memory cosine similarity.
    # Writes to the admin endpoints require `x-api-key == faq_admin_api_key`
    # (constant-time compared); an empty key 401s every write.
    faq_admin_api_key: str = ""
    # --- Phase 5: Agent routing & presence ---
    # Master switch: when False (default) the routing service is bypassed and
    # the static chatwoot_agent_team_id team assignment remains active.
    routing_enabled: bool = False
    # API key that guards write access to the /routing/priorities endpoints.
    # An empty value 401s every write (no unauthenticated mutation).
    routing_admin_api_key: str = ""
    live_faq_collection: str = "live_faq"
    embedding_model: str = "text-embedding-004"

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
    # Label(s) applied to EVERY escalated conversation so agents can filter them
    # in Chatwoot (the live-chat workspace). Kept CRM-neutral — does NOT create a
    # downstream ticket.
    chatwoot_escalation_label: str = "ai-escalation"
    # Complaint-only label: added on top of the escalation label ONLY when the
    # handoff is a genuine complaint (see chatwoot_complaint_reasons / high
    # urgency). This is the label the shared instance's sync turns into a Zammad
    # ticket, so plain "talk to a human" handoffs stay Chatwoot-only and don't
    # spawn a confusing back-office ticket. Empty disables Zammad ticketing.
    chatwoot_complaint_label: str = "escalate"
    # Comma-separated HandoffReason values treated as complaints (-> Zammad ticket).
    # High urgency also counts. Non-complaint reasons (help_request, sales_lead,
    # unknown_retry_limit) stay live-chat-only in Chatwoot.
    chatwoot_complaint_reasons: str = "negative_sentiment"
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
    # When True, our backend creates the Zammad ticket DIRECTLY (via the Zammad
    # REST API) on a complaint escalation and does NOT apply the Chatwoot
    # complaint/`escalate` label — so if the external agent-service sync is ever
    # wired to that label there is no double-ticket. When False (legacy) we fall
    # back to just applying the `escalate` label and let the agent service turn
    # it into a ticket. Requires zammad_enabled.
    zammad_direct_ticketing: bool = True
    # Zammad group the direct-ticketing path files tickets into (must exist in
    # Zammad). "Users" is the default group present on the shared instance.
    zammad_group: str = "Users"

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
        # Chatwoot agent-assist FAQ dashboard app (apps/chatwoot-agent-app) is
        # loaded as an iframe from the Chatwoot host and fetches /kb/suggest +
        # /kb/feedback cross-origin. Allowlist the Chatwoot origin (override via
        # FRONTEND_ORIGINS if the app is hosted elsewhere).
        "http://crm.34-50-103-151.nip.io",
    ]

    # --- Proton assist endpoints -------------------------------------------
    # Shared secret for /assist/* endpoints — must match PROTON_BACKEND_KEY
    # in the tenant env. Empty means the endpoint is unconfigured; boot fails
    # gracefully (endpoints return 503) rather than leaving them open.
    proton_backend_key: str = ""
    # Gemini model for summarize/ask; defaults to the same model as the main agent.
    assist_gemini_model: str = "gemini-2.5-flash"
    # Chatwoot origins allowed to call /assist/* cross-origin. Add each tenant's
    # Chatwoot URL (e.g. http://crm.<IP>.nip.io and proton.crm.<IP>.nip.io).
    assist_cors_origins: list[str] = []

    # --- Ask Copilot (multi-turn agent) ------------------------------------
    # Model for the copilot tool-calling loop; defaults to the assist model.
    copilot_gemini_model: str = "gemini-2.5-flash"
    # Hard cap on tool-call rounds per copilot turn, so a misbehaving model
    # cannot loop forever. Each round is one Gemini call.
    copilot_max_tool_iterations: int = 5
    # Optional allowlist of hostnames that custom webhook tools may call.
    # Empty list (default) = any HTTPS host is permitted (admin-trusted input).
    # Set e.g. ["api.acme.com", "hooks.slack.com"] to restrict outbound calls.
    custom_tool_allowed_hosts: list[str] = []

    # Phase 2 — dept→PIC mapping. JSON object keyed by department slug
    # (matches the dept_<x> label key, e.g. "apps", "sales"). Each value:
    # {pic_name, pic_email, pic_whatsapp, zammad_group, chatwoot_team_id?}.
    # Empty string disables PIC routing (no lookup attempted).
    pic_map_json: str = ""

    # Phase 2 — escalation notifications
    escalation_email_enabled: bool = False
    escalation_cc_pic: bool = True
    escalation_level2_whatsapp: str = ""   # E.164, e.g. "+60112345678"
    escalation_tier2_hours: float = 4.0   # hours after first breach before level-2 alert

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
