from __future__ import annotations

from functools import cache
from typing import Literal

from pydantic import model_validator
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
    # Per-channel first-response (ack) SLA overrides, in MINUTES, as a JSON
    # object keyed by short channel name, e.g.
    # {"whatsapp": 2, "call": 0.333, "facebook": 120, "instagram": 120, "email": 240}.
    # Empty → the global sla_response_hours applies to every channel.
    sla_ack_minutes_by_channel_json: str = ""

    # --- Task Timers & Agent Reminders (Phase 6) ---
    # How many minutes before SLA breach the My-Tasks app shows a warning colour
    # and triggers a desktop notification.
    tasks_reminder_warning_minutes: int = 60
    # When True, the SLA scan job sends a reminder to the global PIC WhatsApp
    # (`sla_pic_whatsapp`) when a conversation is within tasks_reminder_warning_minutes
    # of breach; per-agent routing is deferred. Default False keeps the existing behaviour.
    tasks_reminder_whatsapp_enabled: bool = False
    # API key that gates GET /tasks/mine (which returns customer PII — task subjects
    # are customer names). An empty value 401s every request.
    tasks_api_key: str = ""

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

    # --- pgvector knowledge base (subsystems A+B; default-off) ---
    knowledge_pg_enabled: bool = False
    knowledge_database_url: str = ""
    kb_chunk_size_tokens: int = 800
    kb_chunk_overlap_tokens: int = 100
    kb_score_floor: float = 0.55
    kb_max_upload_bytes: int = 10_485_760  # 10 MiB cap on /kb/knowledge/file uploads
    # RBAC — independent Postgres connection, deliberately NOT shared with
    # knowledge_database_url: RBAC must work without requiring the pgvector KB
    # feature to be enabled. Empty -> require_permission falls back to the
    # existing shared-secret check (today's behavior), no RBAC tables used.
    rbac_enabled: bool = False
    rbac_database_url: str = ""
    # Break-glass bootstrap: the Chatwoot user id to auto-assign the seeded
    # 'administrator' role to on every startup, when set. Without this, a
    # fresh RBAC-enabled DB has an 'administrator' role but nobody assigned to
    # it, and the only way to grant it is POST /authz/roles/{id}/assign —
    # which itself requires roles.manage, a permission nobody has yet. Unset
    # (default) = no auto-assignment; an operator must assign manually (e.g.
    # direct SQL), same as today. Safe to leave set across restarts: the
    # underlying assign is idempotent (checks existence before inserting).
    rbac_bootstrap_admin_user_id: int | None = None

    # RSA (roadside assistance) incident log — own Postgres table, gated the
    # same way the pgvector KB and RBAC are: default-off, needs BOTH flags to
    # activate. Manual staff data entry only, no dispatch-system integration.
    rsa_enabled: bool = False
    rsa_database_url: str = ""

    # --- DMS/TSP integration shell (Package F) ---
    # Wires MockDmsClient into Customer 360's optional `dms` block. Phase 1
    # ships no real DMS adapter, so this is the ONLY thing that can put data
    # in that block — and that data is fabricated demo records. It is the
    # single flag between a demo fixture and a real customer's panel, so it
    # belongs here rather than in a raw os.getenv: anyone auditing a tenant's
    # env must be able to see it, and anyone who needs to turn it off must be
    # able to find it. Off (default) leaves `dms_client=None`, which makes an
    # enabled-but-unwired integration read as "unreachable" (not connected) —
    # never as a misleadingly-empty "ok". Note the integration's own
    # enabled/base_url/credential config is operator-edited in the CRM admin
    # UI and stored in Firestore, NOT here.
    dms_mock_client_enabled: bool = False
    # --- Phase 5: Agent routing & presence ---
    # Master switch: when False (default) the routing service is bypassed and
    # the static chatwoot_agent_team_id team assignment remains active.
    routing_enabled: bool = False
    # API key that guards write access to the /routing/priorities endpoints.
    # An empty value 401s every write (no unauthenticated mutation).
    routing_admin_api_key: str = ""
    # Per-agent round-robin ticket cap: max currently-open conversations an
    # agent may hold before pick_agent skips them. 0 = unlimited (today's
    # behavior, no cap enforced).
    routing_max_concurrent_per_agent: int = 0
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

    # Package D risk mitigation: demo-seeded conversations (deploy/scripts/
    # seed_demo_data/ stamps custom_attributes.demo_seed = <batch_id> on every
    # contact/conversation it creates) otherwise flow into the warehouse and
    # inflate every report. Default False (today's behavior, byte-identical)
    # so Package E's reporting pages have data to show during a demo window;
    # flip True before any real reporting to keep marked conversations out.
    metrics_exclude_demo_seed: bool = False

    # Bot-metrics Phase 4 (manual QA accuracy/quality entry)
    qa_provider: Literal["noop", "bigquery"] = "noop"
    bigquery_qa_labels_table: str = "qa_labels"
    qa_api_key: str = ""

    # Metrics read endpoints (departments / call-centre / lifecycle) — agent/PIC-level
    # aggregates, so gated unlike the channel-only /metrics/dashboard.
    metrics_api_key: str = ""

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
    # urgency), so plain "talk to a human" handoffs stay Chatwoot-only. Empty
    # disables the complaint label entirely.
    chatwoot_complaint_label: str = "escalate"
    # Comma-separated HandoffReason values treated as complaints (-> complaint
    # label + escalation notification). High urgency also counts. Non-complaint
    # reasons (help_request, sales_lead, unknown_retry_limit) stay live-chat-only
    # in Chatwoot.
    chatwoot_complaint_reasons: str = "negative_sentiment"
    # Shared secret required on inbound /webhooks/chatwoot calls (Chatwoot has no
    # built-in HMAC). Compared constant-time; empty leaves the endpoint open.
    chatwoot_webhook_secret: str = ""
    # Domain used to synthesize a customer email on Chatwoot contacts. Web/WhatsApp
    # customers have no real email, but downstream systems that key customers by
    # email need one. Deterministic per session: <sanitized session_id>@<domain>.
    # Use a real TLD so email-format validation passes.
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
    # IVR-4: after each caller utterance, send a short content-free reminder
    # telling the model to re-evaluate the reply language fresh each turn,
    # rather than anchoring to the conversation's established language.
    # Default off -- byte-identical when unset; the exact Live API turn-
    # injection semantics can't be verified without a real call, so this
    # ships gated and can be flipped per-tenant to A/B against today.
    phone_language_nudge_enabled: bool = False
    # Package C Task 3: create the Chatwoot conversation ticket the moment the
    # call starts (instead of only at hangup) and stream the transcript into
    # it live, in flush_seconds-ish batches, instead of writing it all at
    # once when the call ends. Default off -> byte-identical to today: the
    # ticket is created only in finalize(), from the complete transcript.
    phone_transcript_live_enabled: bool = False
    # How often (seconds) the live transcript may flush a completed turn or a
    # still-open turn during a monologue. TranscriptSink enforces this; it's
    # a soft interval, not a guarantee (a flush only happens when something
    # polls it -- see PhoneBridge.pump()).
    phone_transcript_flush_seconds: float = 15.0
    # Package C Task 4: derive case_type/division/concern/status from the
    # completed call transcript via a one-shot Gemini classification, run
    # once in finalize() (never in the live audio path). Default off ->
    # byte-identical to today: status stays the exact "open if handoff else
    # solved" binary rule, and no classification custom attributes are
    # written.
    phone_transcript_classification_enabled: bool = False
    # Package C Task 5: start a dual-channel Twilio recording on the live call
    # (see PhoneBridge._maybe_start_recording) and, once
    # /webhooks/phone/recording-status reports "completed", store the
    # recording sid/duration/url as INTERNAL conversation custom attributes
    # (never a customer-visible comment -- retrieval is meant to be gated
    # behind the `call_recording.listen` permission, see features/authz/
    # seed.py). Default off -> byte-identical to today: handle_twilio's
    # "start" branch never calls CallControl.start_recording.
    #
    # PDPA (Malaysia) requires the caller be told the call is recorded BEFORE
    # recording starts. phone_recording_announcement is that operator-
    # configurable, bilingual (EN + Bahasa Melayu) notice text -- deliberately
    # NOT hard-coded, matching the existing lifecycle/persona message
    # convention. THIS ONE SETTING FAILS CLOSED, unlike everywhere else in
    # this package: if phone_recording_enabled is True but no announcement is
    # configured (or no callback base can be built -- see
    # PhoneBridge._maybe_start_recording), recording is refused entirely
    # (logged at WARNING) rather than started silently.
    #
    # SEQUENCING IS SOLVED (Task 6): the notice is read by a scripted TwiML
    # <Say> that /voice/phone/incoming emits in the same <Response>
    # immediately before <Connect><Stream>. TwiML verbs run in document
    # order, and the Media Stream's "start" event is the only trigger for
    # recording, so the disclosure provably precedes recording -- by
    # construction, not by hope. What is STILL true and worth an operator's
    # attention: it is Twilio's default TTS voice reading the operator's own
    # configured text (no language/voice attributes, so a bilingual notice
    # may be mispronounced in its Bahasa Melayu half), and it is a
    # DISCLOSURE, not a recorded consent capture -- nothing records that the
    # caller heard or agreed to it.
    #
    # DEPENDS ON phone_transcript_live_enabled -- enforced in
    # _phone_flag_dependencies below, which see for why.
    phone_recording_enabled: bool = False
    phone_recording_announcement: str = ""
    # Informational only today (no automated deletion job reads this yet) --
    # recorded here so the retention POLICY is operator-visible and
    # configurable from day one rather than bolted on later.
    phone_recording_retention_days: int = 90
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
    # Package C Task 6: real hand-off of a live call to a human. Default off
    # -> byte-identical to today: request_human_handoff never redirects the
    # call, and keeps answering {"status": "ticket_created"} exactly as
    # before. Phase 1 (this task) resolves a single static hunt-group
    # number (see features/chat/phone/handoff_target.py); a routing-backed
    # per-agent resolver is a second implementation of the same interface,
    # added once the §5.2 decision in the design doc lands -- do not build
    # it speculatively here.
    #
    # Also gates the bot's SPOKEN handoff wording (router.py's phone_stream):
    # off keeps the pre-Package-C "a specialist will follow up" line, since
    # promising a transfer that structurally cannot happen would be a
    # customer-visible change with all flags off. DEPENDS ON
    # phone_transcript_live_enabled -- see _phone_flag_dependencies below.
    phone_handoff_enabled: bool = False
    phone_handoff_target_number: str = ""
    # <Dial timeout> in seconds: how long Twilio rings the target before it
    # gives up and posts DialCallStatus=no-answer to /webhooks/phone/dial-status.
    phone_handoff_timeout_seconds: int = 30
    # Review fix (Critical): <Dial> with no callerId defaults to the parent
    # leg's From. This repo's only wired inbound path is the browser
    # softphone (a TwiML App reached via the Voice JS SDK), where From is
    # `client:<identity>` -- which Twilio REJECTS as a caller id for a PSTN
    # <Number> (error 13214), a TwiML error that terminates the call
    # mid-transfer. HandoffTargetResolver.resolve() treats "handoff enabled
    # but no caller id configured" as resolve-to-None (same fail-safe shape
    # as the no-action-URL guard) rather than dialling blind -- see
    # handoff_target.py.
    phone_handoff_caller_id: str = ""

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
    # {pic_name, pic_email, pic_whatsapp, chatwoot_team_id?,
    # cc_emails?}. cc_emails is a list of extra addresses CC'd on the escalation
    # email (the "relevant personnel"), used when escalation_cc_pic is true.
    # Empty string disables PIC routing (no lookup attempted).
    pic_map_json: str = ""

    # Case category/subcategory taxonomy — JSON object keyed by main-category
    # slug: {"label": str, "subcategories": [str, ...]}. Same fail-open pattern
    # as PIC_MAP_JSON: malformed/empty -> empty taxonomy, classify_ticket_tool
    # falls back to accepting free text (pre-taxonomy behavior). Ships with a
    # working default so the system functions out of the box; override per
    # tenant once the client finalizes their scheme — no code change needed.
    case_taxonomy_json: str = (
        '{"sales":{"label":"Sales","subcategories":["Accessories","Booking",'
        '"Insurance","New Model","Promotion","Refund","Test Drive","Trade In",'
        '"Transfer Ownership","Vehicle Delivery","Vehicle Details",'
        '"Customer Experience"]},'
        '"aftersales":{"label":"Aftersales","subcategories":["Body",'
        '"Roadside Assistance","Service / Recall Campaign","Service Operation",'
        '"Spare Part","Warranty","User Manual","Features"]},'
        '"apps":{"label":"Apps","subcategories":["Information","Operation",'
        '"User ID","No QR Scanner","Notification","Profile","Remote Control"]},'
        '"charging":{"label":"Charging","subcategories":["Home Charging",'
        '"Public Charging"]},'
        '"product":{"label":"Product","subcategories":["Infotainment",'
        '"Telematics"]},'
        '"marketing":{"label":"Marketing","subcategories":["Event / Campaign",'
        '"Partnership / Collaboration","Proposal","Sponsorship"]},'
        '"others":{"label":"Others","subcategories":['
        '"Not Related to Proton e.MAS"]}}'
    )

    # Vehicle-model / product-line dimension — JSON object {"options": [str, ...]}.
    # Same fail-open pattern as CASE_TAXONOMY_JSON. Empty -> the vehicle_model
    # custom attribute is never offered/written (byte-identical to today) —
    # tenants with no product-line concept simply leave this unset.
    vehicle_models_json: str = (
        '{"options": ["e.MAS 5", "e.MAS 7", "e.MAS 7 PHEV", "Not Applicable"]}'
    )

    # Case-type dimension (Inquiry/Complaint/Feedback) — JSON object
    # {"options": [str, ...]}. Same fail-open pattern. Ships with a working
    # default since this concept is fairly universal to support work, but
    # stays configurable/overridable per tenant like every other dimension here.
    case_type_options_json: str = '{"options": ["Inquiry", "Complaint", "Feedback"]}'

    # SOP resolution-time targets, in working hours, per case_type. JSON:
    # {"<case_type lowercased>": {"buckets_wh": [int, ...], "labels": [str, ...]}}.
    # buckets_wh are the upper edges (exclusive) of every bucket except the
    # last, which is open-ended; labels must have exactly one more entry than
    # buckets_wh. Malformed/missing entries fall back to being excluded from
    # v_resolution_sla_buckets (that case_type's rows simply won't bucket).
    resolution_sla_targets_json: str = (
        '{"inquiry": {"buckets_wh": [8], "labels": ["Within 8wh", ">8wh"]},'
        '"complaint": {"buckets_wh": [24, 48, 72], '
        '"labels": ["<24wh", "24-48wh", "48-72wh", ">72wh"]},'
        '"feedback": {"buckets_wh": [48], "labels": ["Within 48h", ">48h"]}}'
    )

    # Phase 2 — escalation notifications
    escalation_email_enabled: bool = False
    # When true (default), escalation emails CC the department's configured
    # cc_emails (the "relevant personnel" beyond the To-recipient PIC).
    escalation_cc_pic: bool = True
    escalation_level2_whatsapp: str = ""  # E.164, e.g. "+60112345678"
    escalation_tier2_hours: float = 4.0  # hours after first breach before level-2 alert

    # EM-7: two-thread email escalation for natively-escalated Email-channel
    # conversations (agent applies the `escalate` label). Independent of the
    # AI-driven escalation_email_enabled/escalation_cc_pic pair above, which
    # covers a different trigger (the AI's own autonomous handoff decision).
    email_escalation_ack_enabled: bool = False
    email_escalation_ack_template: str = (
        "Your case has been escalated to a specialist team who will follow up shortly."
    )
    # dealer slug -> email, e.g. {"kl_pj": "kl-pj-service@dealer.example"}.
    # Empty (default) means no dealer email is ever sent.
    dealer_email_map_json: str = ""

    # Reply-To template for escalation mail, e.g.
    # "support+case{conv_id}@proton.example". Empty (default) means no
    # Reply-To and no [CASE-n] subject tag -- mail is byte-identical to
    # pre-reply-loop behavior. `{conv_id}` is the only placeholder.
    escalation_reply_to_template: str = ""

    # Settings configurations
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @model_validator(mode="after")
    def _phone_flag_dependencies(self) -> Settings:
        """Package C whole-branch review fix (Important 10): recording and
        handoff DEPEND on phone_transcript_live_enabled -- fail fast rather
        than lose a callback's write.

        Both features have a Twilio callback that fires after the call and
        resolves its conversation with ``find_conversation_ticket``, which
        never creates. With ``phone_transcript_live_enabled`` off, the
        conversation only comes into existence inside ``PhoneBridge.
        finalize()``. Twilio's recording-status callback, or a 30s
        ``no-answer`` dial-status, can easily win that race -- classification
        alone can add up to 10s to finalize() -- and when it does,
        ``find_conversation_ticket`` returns ``None``, the handler answers
        **200**, and Twilio never retries. A recording, or an owed
        ``unanswered_handoff`` tag, is then silently gone with only a log
        line.

        Making the dependency structural is the only fix that cannot be
        misconfigured: with the ticket created at the Twilio "start" event,
        it always exists before either callback can fire. Enforced here so a
        bad combination is a startup error with a readable message (see
        ``create_app``/``get_settings``, both of which construct Settings
        eagerly), never a silently-dropped write in production.
        """
        missing = [
            name
            for name, enabled in (
                ("PHONE_RECORDING_ENABLED", self.phone_recording_enabled),
                ("PHONE_HANDOFF_ENABLED", self.phone_handoff_enabled),
            )
            if enabled
        ]
        if missing and not self.phone_transcript_live_enabled:
            raise ValueError(
                f"{'/'.join(missing)} requires PHONE_TRANSCRIPT_LIVE_ENABLED=true. "
                "Those features' Twilio callbacks resolve the conversation by session "
                "id and never create one, so without the ticket being created at call "
                "start they can fire before it exists and their write (recording "
                "attachment, unanswered_handoff tag) is silently lost -- Twilio does "
                "not retry a 200. Enable PHONE_TRANSCRIPT_LIVE_ENABLED first."
            )
        return self


@cache
def get_settings() -> Settings:
    """Helper function to load and cache application settings."""
    return Settings()
