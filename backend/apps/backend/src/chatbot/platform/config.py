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
    # P1: measure every SLA threshold on the inbox's configured business hours
    # instead of the wall clock. With this ON, a target of "2 hours" means 2
    # WORKING hours -- a case arriving 18:00 Friday breaches on Monday morning,
    # not at 20:00 Friday. Existing configured targets do NOT need changing;
    # they were always intended as working-hours targets.
    #
    # Default False, and with it off the arithmetic is byte-identical to the
    # pre-P1 engine (asserted in test_sla_clock.py). This engine drives live
    # breach alerts on a production tenant, so the dark path is load-bearing.
    # Per-inbox override: SlaPolicyRepository.resolve().working_hours_enabled.
    sla_working_hours_enabled: bool = False
    # Let an explicit ACKNOWLEDGED audit transition (recorded by the escalation
    # reply linker via POST /escalation/acknowledge) satisfy the first-response
    # SLA, so "acknowledge the customer" and "update the customer" are two
    # measurable events rather than one. Default False = first-agent-reply is
    # the only signal, exactly as before. Can only ever suppress an ack breach.
    sla_acknowledgement_enabled: bool = False
    # P2: the customer acknowledgement posted into the conversation thread when
    # an escalation happens on a non-Email channel. Blank (the default) means
    # no chat ack is posted -- an operator emptying it is opting out, not
    # asking for an empty message at the customer.
    escalation_ack_chat_template: str = ""
    # Inboxes the SLA engine scans. Empty (default) = the single
    # chatwoot_inbox_id, preserving pre-existing behavior exactly. A
    # comma-separated list scans each. "*" scans every inbox in the account
    # -- needed for the email escalation timers, since the Email inbox is
    # normally not chatwoot_inbox_id.
    sla_inbox_ids: str = ""
    # SLA breach/reminder delivery beyond the WhatsApp ping. Email goes to
    # the department PIC group resolved from the conversation's dept_<slug>
    # label; the note is a private message on the conversation itself. Both
    # default False so an unconfigured deployment keeps today's behaviour
    # (WhatsApp-only, or nothing if sla_pic_whatsapp is also empty).
    sla_alert_email_enabled: bool = False
    sla_alert_note_enabled: bool = False

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

    # --- P6: agent presence, custom statuses & workforce dashboard ---
    # Seven independent flags, all off by default, so a first deploy of this
    # package is byte-identical to today. Note routing_enabled (above) is
    # NOT one of them and P6 does not change its default -- a routing engine
    # is switched on deliberately, per tenant, same as before this package.
    #
    # Off = no poller, no presence events -- the missing primitive (§3.1 of
    # the P6 design) that every other P6 flag below builds on.
    presence_tracking_enabled: bool = False
    # Off = only the three native Chatwoot statuses (online/busy/offline)
    # exist; the eight-status catalogue (Available, Busy, Lunch, Break,
    # Coaching, Training, Toilet, Prayer) is not offered anywhere.
    presence_custom_statuses_enabled: bool = False
    # Off = no 10-minute / 1-hour unavailability alerts fire, regardless of
    # presence_tracking_enabled.
    presence_threshold_alerts_enabled: bool = False
    # Off = no After-Call-Work state exists; a call ending never places an
    # agent into a wrap-up status.
    acw_enabled: bool = False
    # Off = pick_agent keeps today's first-match-in-iteration-order
    # selection. On = fewest-open-conversations wins within a tier, tie
    # broken by least-recently-assigned.
    routing_fair_share_enabled: bool = False
    # Off = assignment stays purely event-driven (pick_agent runs only at
    # handoff time); no sweeper polls for unassigned work sitting idle.
    routing_sweep_enabled: bool = False
    # Off = the follow-up-date panel is hidden and no follow_up_at custom
    # attribute is read or written.
    follow_up_date_enabled: bool = False

    # Tuning values for the flags above. Each is inert while its owning flag
    # is off, so shipping non-default tunables ahead of enabling a flag is
    # safe.
    #
    # How often the presence poller ticks. Chatwoot has no presence webhook,
    # so this is the only way to see a status change made in Chatwoot's own
    # UI. The thresholds it drives are 10 minutes and 1 hour, so a minute of
    # granularity is well inside the noise; tighter polling would only
    # multiply Chatwoot API calls for no decision-relevant precision.
    presence_poll_seconds: int = 60
    # Elapsed time in a counts_as_unavailable status before the agent (and
    # admin) are notified.
    presence_warn_minutes: int = 10
    # Elapsed time in a counts_as_unavailable status before the admin gets
    # the escalated alert (with the agent's WIP list attached).
    presence_escalate_minutes: int = 60
    # An agent who forgets to leave After-Call-Work would otherwise be
    # excluded from routing indefinitely -- a self-inflicted outage that
    # would get blamed on the routing engine. This is the auto-exit timeout.
    acw_timeout_seconds: int = 120
    # Minimum age (seconds) an unassigned conversation must reach before the
    # sweeper will assign it. This is the race guard from §3.6 of the P6
    # design: without it, the sweeper and the event-driven assignment path
    # (which runs at handoff time, essentially immediately) would both try
    # to assign the same fresh conversation. 120s is chosen to comfortably
    # exceed that event-driven path's own latency, which is the only race
    # this gate exists to prevent.
    routing_sweep_min_age_seconds: int = 120
    # How often the sweeper itself ticks.
    routing_sweep_interval_seconds: int = 60

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
    # falls back to accepting free text (pre-taxonomy behavior). Default is
    # the client's own taxonomy: RFP 2026_028's "APPENDIX A - Case
    # Categorisation" (see docs/client-materials/RFP 2026_028/
    # case-categorisation.json for the full 6-column, 300-row transcription
    # this is derived from) — division slug -> {label: RFP "Case Division"
    # value, subcategories: RFP "Level 1" values for that division}. Level
    # 2/3/4 have no representation here yet (tracked as a follow-up, not
    # built by this change). Two subcategory strings differ only in
    # whitespace around a slash ("Service/Recall Campaign" vs "Service /
    # Recall Campaign") because the client's own PDF spells the same After
    # Sales concept both ways on different rows — preserved verbatim rather
    # than silently merged. SAME value as agent/'s CASE_TAXONOMY_JSON.
    case_taxonomy_json: str = (
        '{"sales":{"label":"Sales","subcategories":["Delivery","Refund",'
        '"Customer Experience","Promotion","Test Drive","Outlet","Booking",'
        '"Insurance","Finance Information","Vehicle Details","New Model",'
        '"Custom EV Plate (Frame)","Staff","Sales Facilities","Others"]},'
        '"product":{"label":"Product","subcategories":["Infotainment",'
        '"Accessories","Vehicle Specification","Vehicle Model",'
        '"Specification","Performance","Features","Others"]},'
        '"network":{"label":"Network","subcategories":["Outlet Facilities"]},'
        '"charging":{"label":"Charging","subcategories":["Infotainment",'
        '"Others","Home Charging","Charging Credit","Public Charging",'
        '"Compatibility","Configuration","Booking","Refund","Billing",'
        '"Vendor"]},"apps":{"label":"Apps",'
        '"subcategories":["Apps Synchronization","Service Function",'
        '"Remote Control","Unable to update.","Dealer Information","E-Mall",'
        '"Notification","Vehicle Order Issue","smart points","Function",'
        '"Finance Calculator","Test Drive Order Issue","Profile","Others",'
        '"User ID","Unable to register","Operation","Apps Information",'
        '"Point system","Information","Content"]},'
        '"aftersales":{"label":"After Sales",'
        '"subcategories":["Brake / Electronic Parking Brake","Airconditioner",'
        '"Steering","Suspension","Cooling System","Body","Electrical","ADAS",'
        '"Features","Airbag","Others","Spare Part","Warranty",'
        '"Service Operation","Technical","Service/Recall Campaign",'
        '"Service / Recall Campaign","Roadside Assistance","User Manual",'
        '"Staff","Service Facilities"]},"others":{"label":"Others",'
        '"subcategories":["Misdial","Job Vacancy","Scams/Spams",'
        '"No Respond From Customer","Call Disconnected","Test Call"]},'
        '"marketing":{"label":"Marketing","subcategories":["Merchandise",'
        '"Charging Credit","Event/Campaign","Request to Collab",'
        '"Marketing inquiry","Sponsorship"]}}'
    )

    # Vehicle-model / product-line dimension — JSON object {"options": [str, ...]}.
    # Same fail-open pattern as CASE_TAXONOMY_JSON. Empty -> the vehicle_model
    # custom attribute is never offered/written (byte-identical to today) —
    # tenants with no product-line concept simply leave this unset.
    vehicle_models_json: str = (
        '{"options": ["e.MAS 5", "e.MAS 7", "e.MAS 7 PHEV", "Not Applicable"]}'
    )

    # Case-type dimension (Inquiry/Complaint/Compliment & Feedback) — JSON
    # object {"options": [str, ...]}. Same fail-open pattern. Ships with the
    # client's exact RFP 2026_028 Appendix A "Case Category" values (note
    # "Compliment & Feedback", not "Feedback"), but stays
    # configurable/overridable per tenant like every other dimension here.
    case_type_options_json: str = '{"options": ["Inquiry", "Complaint", "Compliment & Feedback"]}'

    # Level-2 (+ folded Level 3/4) case detail — RFP 2026_028 Appendix A's
    # fourth taxonomy tier stored in a THIRD custom attribute, "case_detail"
    # (not three more attributes for Level 2/3/4 separately). JSON object
    # {"options": [str, ...]}, the SAME shape and fail-open pattern as
    # CASE_TYPE_OPTIONS_JSON/VEHICLE_MODELS_JSON — reused as-is by
    # option_lists.py's OptionList/build_option_list, no new
    # parsing code needed.
    #
    # Each value is prefix-encoded against the FULL case_subcategory value
    # ("<Case Division>: <Level 1>"), one level deeper than case_subcategory
    # is against case_category, using the same ": " join:
    # "<Case Division>: <Level 1>: <Level 2>". Where the RFP row also has a
    # Level 3 and/or Level 4, they are folded into that SAME value (never a
    # 4th/5th picker) with " — " (em dash) appended in order: "...<Level
    # 2> — <Level 3> — <Level 4>", e.g. "Sales: Refund: Booking — Status
    # — Dealer Refund". An em dash was chosen over "/" (already used
    # inside several Level 2 strings themselves, e.g. "Roadside Assistance:
    # Panel Workshop/Location") and over a second ": " (which would collide
    # visually with the division/level-1 join).
    #
    # Full-chain prefixing (NOT Level-1-only, e.g. NOT "Delivery: No
    # Estimated Time Delivery") is deliberate: Level 1 names repeat across
    # divisions with their OWN distinct Level 2 sets — "Refund" under both
    # Sales and Charging, "Infotainment" under both Product and Charging,
    # "Charging Credit" under both Marketing and Charging, "Booking" under
    # both Sales and Charging. A Level-1-only prefix would mix unrelated
    # divisions' Level 2 options together in the sidebar cascade
    # (deploy/chatwoot-fork/patches/0050-case-detail-hierarchy.patch).
    #
    # 246 unique values, covering the 258 of 300 RFP rows that carry a
    # Level 2 (the remaining 42 rows have no Level 2 in the source PDF, so
    # no case_detail option is generated for them — their Level 1 alone,
    # via case_subcategory, is the full available detail). Generated from
    # docs/client-materials/RFP 2026_028/case-categorisation.json, not
    # hand-transcribed — see that file for the source-of-truth rows this
    # is derived from. SAME value as agent/'s CASE_DETAIL_OPTIONS_JSON
    # (both services parse it independently).
    case_detail_options_json: str = (
        '{"options":["Sales: Delivery: No Estimated Time Delivery","Sales: Refund: Delay","Sales: '
        'Customer Experience: Poor Buying Experience — Sale Specialist Profesionalism","Sales: Del'
        'ivery: No Allocation - Delay","Sales: Delivery: Overpromised - ETD","Sales: Refund: No Re'
        'cords","Sales: Refund: Others","Sales: Promotion: Ended Promotion","Sales: Test Drive: No'
        't Available","Sales: Test Drive: Others","Product: Infotainment: QR Code Broken","Product'
        ': Infotainment: Account not exist","Product: Infotainment: App Fault Notification","Produ'
        'ct: Infotainment: Data not Sync","Product: Infotainment: 3rd party Apps Update","Product:'
        ' Infotainment: OTA","Product: Infotainment: Malfunction","Product: Infotainment: Others",'
        '"Product: Infotainment: Display Malfunction","Product: Infotainment: No GPS Signal","Netw'
        'ork: Outlet Facilities: Ambience/Environment","Network: Outlet Facilities: Refreshment","'
        'Network: Outlet Facilities: Staff Grooming","Network: Outlet Facilities: Furniture & Fitt'
        'ings","Charging: Infotainment: Charging abnormally Interupted","Charging: Home Charging: '
        'Assessment","Charging: Home Charging: Installation","Charging: Charging Credit: Not Entit'
        'le","Charging: Charging Credit: Didn’t received Points","Charging: Charging Credit: other'  # noqa: RUF001
        's","Charging: Public Charging: Map Innacurate location","Charging: Public Charging: Price'
        '","Charging: Public Charging: Car DHU Map not Updated","Charging: Public Charging: smart '
        'point charging","Charging: Public Charging: others","Charging: Configuration: Charging sp'
        'eed","Charging: Configuration: Electical phase","Charging: Home Charging: Function","Char'
        'ging: Home Charging: Quality","Charging: Home Charging: Warranty","Charging: Home Chargin'
        'g: Others","Charging: Public Charging: Power supply Issue","Charging: Public Charging: Lo'
        'ose Cable","Charging: Public Charging: Pre-Authorization","Charging: Public Charging: Oth'
        'ers","Apps: Service Function: Apps Stuck / Frozen","Apps: Service Function: Payment","App'
        's: Remote Control: Unable to view data balance","Apps: Remote Control: Others","Apps: Una'
        'ble to update.: Apps Version","Apps: Remote Control: Climate / Weather","Apps: Remote Con'
        'trol: Car Location","Apps: Dealer Information: Contact not updated","Apps: Dealer Informa'
        'tion: Innacurate location","Apps: Service Function: Unable to received OTP","Apps: Servic'
        'e Function: Unable to register","Apps: Service Function: Others","Apps: E-Mall: Order Ful'
        'fillment","Apps: E-Mall: Order not received","Apps: E-Mall: Order not found","Apps: E-Mal'
        'l: Price","Apps: E-Mall: Merchandise Quality","Apps: E-Mall: Merchandise Stock","Apps: E-'
        'Mall: Others","Apps: Notification: Error","Apps: Notification: Others","Apps: Vehicle Ord'
        'er Issue: Others","Apps: Vehicle Order Issue: Order Synhcronization","Apps: smart points:'
        ' Validity","Apps: smart points: Unable to use","Apps: smart points: Others","Apps: Functi'
        'on: Data info not updated","After Sales: Brake / Electronic Parking Brake: Faulty","After'
        ' Sales: Brake / Electronic Parking Brake: Noise","After Sales: Brake / Electronic Parking'
        ' Brake: Judder","After Sales: Brake / Electronic Parking Brake: Warning Light","After Sal'
        'es: Brake / Electronic Parking Brake: Burned Smell","After Sales: Brake / Electronic Park'
        'ing Brake: Others","After Sales: Airconditioner: Smell","After Sales: Airconditioner: Fau'
        'lty","After Sales: Airconditioner: Leakage","After Sales: Airconditioner: Not Cold","Afte'
        'r Sales: Airconditioner: Others","After Sales: Steering: Noise","After Sales: Steering: V'
        'ibration","After Sales: Steering: Position","After Sales: Steering: Heavy","After Sales: '
        'Steering: Alignment","After Sales: Suspension: Leakage","After Sales: Suspension: Noise",'
        '"After Sales: Suspension: Hard","After Sales: Suspension: Alignment","After Sales: Suspen'
        'sion: Others","After Sales: Cooling System: Smell","After Sales: Cooling System: Leakage"'
        ',"After Sales: Cooling System: Others","After Sales: Body: Noise — Interior","After Sales'
        ': Body: Dented — Interior","After Sales: Body: Alignment — Interior","After Sales: Body: '
        'Gap — Interior","After Sales: Body: Leakage — Interior","After Sales: Body: Rusty — Inter'
        'ior","After Sales: Body: Vibration — Interior","After Sales: Body: Others — Interior","Af'
        'ter Sales: Body: Noise — Exterior","After Sales: Body: Dented — Exterior","After Sales: B'
        'ody: Allignment — Exterior","After Sales: Body: Gap — Exterior","After Sales: Body: Leaka'
        'ge — Exterior","After Sales: Body: Rusty — Exterior","After Sales: Body: Vibration — Exte'
        'rior","After Sales: Body: Others — Exterior","After Sales: Electrical: Failure","After Sa'
        'les: Electrical: Improper Function","After Sales: Electrical: Noise","After Sales: Electr'
        'ical: Others","After Sales: ADAS: Failure","After Sales: ADAS: Warning Light","After Sale'
        's: ADAS: Others","After Sales: Features: Failure","After Sales: Features: Improper Functi'
        'on","After Sales: Features: Safety Features","After Sales: Features: Others","After Sales'
        ': Airbag: Airbag not deploy","After Sales: Airbag: Airbag sudden deploy","After Sales: Ai'
        'rbag: Recall","After Sales: Airbag: Others","After Sales: Spare Part: Parts Arrival Delay'
        '","After Sales: Spare Part: No Stock","After Sales: Spare Part: Price","After Sales: Spar'
        'e Part: Others","After Sales: Spare Part: Quality","After Sales: Warranty: Rejected","Aft'
        'er Sales: Warranty: Status","After Sales: Warranty: Period","After Sales: Service Operati'
        'on: General Repair","After Sales: Service Operation: Accident Repair","After Sales: Servi'
        'ce Operation: Insurance Claim","After Sales: Service Operation: Repair Quality","After Sa'
        'les: Service Operation: Service Profesionalism","After Sales: Service Operation: Courtesy'
        ' Car","After Sales: Service Operation: Delay Vehicle Repair","After Sales: Service Operat'
        'ion: Outlet Facilities","After Sales: Service Operation: Others","After Sales: Technical:'
        ' Major Repair","After Sales: Technical: Others","After Sales: Technical: Comeback Job","A'
        'fter Sales: Service/Recall Campaign: Information","After Sales: Service/Recall Campaign: '
        'Others","Sales: Outlet: Contact","Sales: Outlet: Location","Sales: Outlet: Others","Sales'
        ': Booking: Fees","Sales: Booking: Status","Sales: Booking: Information","Sales: Booking: '
        'Cancellation","Sales: Booking: Others","Sales: Delivery: Vehicle ETD","Sales: Delivery: S'
        'tock Availability","Sales: Delivery: Others","Sales: Refund: Booking — Status — Dealer Re'
        'fund","Sales: Refund: Booking — Status — HQ Refund","Sales: Refund: Booking — Timeframe",'
        '"Sales: Insurance: Insurance Package","Sales: Insurance: Insurance Renewal","Sales: Insur'
        'ance: Insurance Coverage","Sales: Insurance: Other Insurance Matters","Sales: Test Drive:'
        ' Appointment Booking","Sales: Test Drive: Appointment Cancellation","Sales: Finance Infor'
        'mation: Bank Loan","Sales: Finance Information: Loan Tenure","Sales: Finance Information:'
        ' Others","Sales: Vehicle Details: Price","Sales: Vehicle Details: Colour","Sales: Vehicle'
        ' Details: Others","Sales: Promotion: Ongoing Promotion","Sales: Promotion: Irrelevant Pro'
        'motion","Sales: Promotion: Misleading Promotion","Sales: New Model: Launch Date","Sales: '
        'New Model: Price","Sales: New Model: Brochure","Sales: New Model: Promotion","Sales: New '
        'Model: Release Date","Product: Accessories: Package","Product: Accessories: Delivery Stat'
        'us","Product: Accessories: Others","Product: Infotainment: Vehicle Settings","Product: In'
        'fotainment: Data Information","Product: Infotainment: Software Updates","Product: Infotai'
        'nment: Charging Map Display","Marketing: Merchandise: Package","Marketing: Merchandise: D'
        'elivery Status","Marketing: Merchandise: Others","Marketing: Charging Credit: Not Entitle'
        '","Marketing: Charging Credit: Didn’t received","Charging: Booking: WallBox — Assessment"'  # noqa: RUF001
        ',"Charging: Booking: WallBox — Installation","Charging: Refund: Home Assesment — Status",'
        '"Charging: Refund: Home Assesment — Timeframe","Charging: Billing: Charging Billing","Cha'
        'rging: Home Charging: Price","Charging: Home Charging: Purchase Matter","Charging: Home C'
        'harging: Assessment — Status","Charging: Home Charging: Assessment — Reschedule","Chargin'
        'g: Home Charging: Installation — Status","Charging: Home Charging: Installation — Resched'
        'ule","Charging: Public Charging: Charging Location","Charging: Public Charging: Charger M'
        'alfunction","Charging: Public Charging: Promotion","Apps: User ID: Forget User ID","Apps:'
        ' User ID: Information","Apps: User ID: New Account ID","Apps: User ID: Others","Apps: Ope'
        'ration: Vehicle Registration","Apps: Operation: Data Balance Information","Apps: Operatio'
        'n: Finance Calculator","Apps: Point system: Validity","Apps: Point system: Redemption","A'
        'pps: Point system: others","Apps: Information: Vehicle data not updated","Apps: Informati'
        'on: Info / Event not updated","Apps: Operation: Test Drive Order Issue","Apps: Informatio'
        'n: Profile","After Sales: Spare Part: Stock Availbility","After Sales: Spare Part: Parts '
        'Information","After Sales: Spare Part: ETA","After Sales: Warranty: Warranty Information"'
        ',"After Sales: Warranty: Warranty Coverage","After Sales: Warranty: Warranty Period","Aft'
        'er Sales: Warranty: Extended Limited Warranty","After Sales: Warranty: Others","After Sal'
        'es: Service Operation: Vehicle Repair/Service Status","After Sales: Service Operation: Re'
        'gular Maintenance Service Time","After Sales: Service Operation: Service Appointment","Af'
        'ter Sales: Service Operation: Service / Repair Price","After Sales: Service Operation: Se'
        'rvice Advisor\'s Professionalism/Attitude","After Sales: Service / Recall Campaign: Infor'
        'mation","After Sales: Service / Recall Campaign: Others","After Sales: Roadside Assistanc'
        'e: Request for RSA Contact","After Sales: Roadside Assistance: Service Provided","After S'
        'ales: Roadside Assistance: Panel Workshop/Location","After Sales: Roadside Assistance: To'
        'w Truck Varieties","After Sales: Roadside Assistance: Towing Charges","After Sales: Roads'
        'ide Assistance: Others","After Sales: User Manual: Obtain User Manual","After Sales: User'
        ' Manual: Car Features"]}'
    )

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
    # P2: the same idea on the dealer forward, which had no CC at all. Default
    # FALSE, not True like escalation_cc_pic -- the dealer leg carries the full
    # transcript to an address outside the company, so widening its recipient
    # list has to be a deliberate act.
    escalation_cc_dealer: bool = False
    # P2: byte budget for attachments carried into the PIC/dealer escalation
    # mail (photos of the damaged part, PDFs). 0 (default) disables the feature
    # entirely and costs not even an API call. Applies to the whole mail, not
    # per file; newest attachments win when it cannot fit everything, and what
    # was left out is described in the body rather than silently dropped.
    escalation_attachment_budget_bytes: int = 0
    # P2 (§4.39, SMTP half): post a private note on the conversation when an
    # escalation leg fails to send, so the operator learns the PIC was never
    # told instead of seeing an `escalate` label and assuming it worked.
    # Private always -- the customer must never be shown our SMTP problems.
    escalation_failure_note_enabled: bool = False
    # P2 task 6: consult Chatwoot agent availability when escalating, so an
    # offline PIC's online colleagues are told too. Can only ever WIDEN the
    # recipient list -- the PIC is always included. Off by default because it
    # costs an extra API call per escalation.
    escalation_presence_check_enabled: bool = False

    # Addresses this service must NEVER send to, comma-separated. Enforced in
    # SmtpEmailSender itself, so no routing record, env var, stale automation
    # rule or hand-typed CC can get past it. Exists because sustained delivery
    # failures to a dead domain get the sending account rate-limited, which
    # takes every real escalation down with it.
    email_blocked_recipients: str = ""

    # P4: the calendar the report buckets use. Defaults to UTC, and that
    # default is the IDENTITY TRANSFORM -- the emitted view DDL is
    # byte-identical to pre-P4, so no existing dashboard number moves until an
    # operator deliberately changes this. Switching it RE-BUCKETS EVERY
    # HISTORICAL FIGURE on every chart in one deploy: run
    # scripts/compare-reporting-timezone.py first and keep the output.
    # Validated against an allowlist at view-creation time, so a typo fails
    # where somebody is watching rather than at query time on a dashboard.
    reporting_timezone: str = "UTC"

    # P5: the control-item slide (14 metrics vs 14 targets) and its
    # operator-editable targets store. Off = the endpoint is not mounted.
    control_items_enabled: bool = False
    # Seed the targets store from RESOLUTION_SLA_TARGETS_JSON at startup.
    # Creates only; never overwrites an operator edit, so it is safe to leave
    # on -- but off by default so a first deploy changes nothing.
    targets_seed_enabled: bool = False

    # P3: the case-record panel in the conversation sidebar (case_detail,
    # vehicle plate/chassis, WIP notes...) and its GET/PATCH endpoints. Off
    # means the endpoints 404, so the fork panel simply does not render rather
    # than showing a permissions error on every conversation.
    case_fields_enabled: bool = False
    # Attach a coverage note to any report block grouped by one of these
    # sparse, agent-entered fields. ON by default, unlike everything else
    # here: a chart built on a half-filled column that does not say so invites
    # precisely the wrong conclusion.
    report_coverage_disclosure: bool = True
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

    # --- P7: AI conversational quality --------------------------------------
    # Nine settings, all default-off/default-zero, so a tenant that sets none
    # of them gets byte-identical behaviour to before P7: sentiment stays
    # unclassified, no translate action exists, FAQ ranking is pure semantic
    # search, media attachments get today's generic instruction, and nothing
    # is auto-summarised or indexed on resolve.

    # Task 1: classify sentiment per turn (an extra tool argument on the
    # existing per-turn Gemini call, never a second round-trip) and write
    # session_state["sentiment"], which the existing ticket-creation gate and
    # API response already read. Off = the field stays None, exactly as today.
    sentiment_classifier_enabled: bool = False
    # Task 2: replace the static "## Tone" persona paragraph with one selected
    # by the current sentiment (operator-editable in Knowledge Settings).
    # Inert without sentiment_classifier_enabled above. Off = the static
    # paragraph, unchanged.
    sentiment_tone_adjustment_enabled: bool = False
    # Task 3: the agent-facing translate action (POST /assist/translate) that
    # renders an inbound message in the agent's own language as a private
    # note, on demand. Off = no translate action exists.
    translation_enabled: bool = False
    # Task 3: OUTBOUND Tamil replies to CUSTOMERS specifically. This is a
    # separate, one-directional gate and is NOT symmetric with
    # translation_enabled above: inbound Tamil translation (so an agent can
    # READ a Tamil message) is enabled by translation_enabled alone and is
    # useful now -- an imperfect translation an agent reads is far better
    # than a message they cannot read at all. Outbound Tamil sends unverified
    # machine translation directly to a customer in a regulated automotive-
    # support context, and ships disabled on purpose pending a signed-off
    # evaluation (30 real Tamil enquiries, scored by a Tamil speaker). Do not
    # flip this just because translation_enabled is on -- keep it False until
    # that evaluation exists.
    translation_outbound_tamil_enabled: bool = False
    # Task 4: weight given to a keyword-overlap score (against the authored
    # FAQ `keywords` field) blended into `_rank`'s cosine-similarity ranking.
    # 0.0 is not merely "off" -- it is required to reproduce today's ranking
    # EXACTLY, entry for entry and score for score. That equivalence is the
    # entire safety argument for shipping hybrid ranking onto a live tenant:
    # a defaulted call must be provably identical to the pre-P7 call, not
    # just similar. Raise it only after the calibration evaluation set
    # (task 10) shows a net improvement -- a large weight would regress the
    # common case (semantic search, which already works) to fix the rare one
    # (exact codes like e.MAS7 that embed poorly).
    faq_keyword_weight: float = 0.0
    # Task 7: a transient, dismissible FAQ-suggestion strip rendered above the
    # fork's composer when a suggestion's confidence exceeds a threshold.
    # Off = side panel only -- an agent's typing path is left alone by
    # default.
    faq_suggestion_popup_enabled: bool = False
    # Task 8: append a media-diagnosis instruction (describe what's visible,
    # name the likely fault, state a confidence level, ask at most one
    # follow-up question) whenever an image/video part reaches the model.
    # Off = today's generic instruction, unchanged.
    media_diagnosis_prompt_enabled: bool = False
    # Task 9: index a resolved conversation's SUMMARY (never its transcript)
    # into its own pgvector namespace, separate from the authored FAQ corpus,
    # so it can be purged independently of curated content and its
    # suggestions labelled in the panel as machine-generated rather than
    # approved guidance. This is a PII mitigation, not PII masking (the real
    # fix is R16, blocked on Q7) -- off by default for that reason.
    # Off = nothing indexed.
    resolved_case_index_enabled: bool = False
    # Task 9: fire the existing agent-triggered POST /assist/summarize
    # automatically on the resolve event and post the summary as a private
    # note; idempotent per resolve -- a case resolved, reopened and resolved
    # again appends a second summary rather than overwriting the first. Off =
    # summaries stay agent-triggered only, exactly as today. Independent of
    # resolved_case_index_enabled above -- the two share a resolve-event
    # handler, not a dependency.
    auto_summary_on_resolve_enabled: bool = False

    # --- P8: AI & agent measurement -----------------------------------------
    # Six settings, all default-off/default-zero, so a tenant that sets none
    # of them gets byte-identical behaviour to before P8: no Gemini call here
    # is metered, no cost view exists, no NPS question is ever asked, CSAT
    # stays channel-level only, and QA stays the existing channel-agnostic
    # human rubric.

    # Task 2: master switch for the metering wrapper every backend Gemini
    # call site is routed through (the client-boundary wrapper, not a
    # per-call-site change -- see metered_genai.py). Off = the wrapper
    # records nothing and adds no latency; the backend keeps recording zero
    # token counts, exactly as today. On = every wrapped call writes a
    # TokenUsage row.
    #
    # MISSING USAGE METADATA RECORDS None, NEVER 0. A zero-token call is a
    # real, observed thing; "we did not capture it" is a different thing, and
    # a cost report that conflates the two UNDERSTATES SPEND, silently, in
    # the direction that looks good. Every reader of token_usage.py or
    # ai_actions' output_tokens/cached_tokens columns must preserve this
    # distinction -- do not "simplify" a missing value to 0.
    token_metering_enabled: bool = False
    # Task 4: mounts GET /metrics/ai-cost and the v_ai_cost view (day x
    # service x surface x model). Off = no cost endpoint, no view. Meaningful
    # only once token_metering_enabled has been on long enough to have rows
    # to report on; an unpriced model is reported as "unpriced", never as
    # free, so a new model never silently shows a $0 cost.
    ai_cost_reporting_enabled: bool = False
    # Task 5: fraction (0.0-1.0) of end-of-conversation surveys that ask NPS
    # ("how likely are you to recommend") INSTEAD OF CSAT, for the sampled
    # fraction -- never both, since asking both halves the response rate for
    # each. 0.0 (default) = no NPS question is ever asked, exactly as today;
    # v_nps_by_agent stays correctly empty rather than sparse. Attribution is
    # to the agent assigned at the moment the survey is answered, recorded
    # then and never re-derived from a later reassignment.
    nps_sample_rate: float = 0.0
    # Task 6: mounts v_csat_by_agent, a sibling of v_csat (which this does
    # NOT change -- existing dashboards read it). Off = CSAT stays
    # channel-level only, byte-identical to today.
    #
    # EVERY RATE METRIC RETURNS ITS DENOMINATOR, NO EXCEPTIONS. A per-agent
    # score without a sample-size count is how a measurement becomes an
    # industrial-relations grievance -- v_csat_by_agent always returns the
    # rating count alongside the average, flag on or off.
    csat_by_agent_enabled: bool = False
    # Task 6: an agent with fewer ratings than this is excluded from ranked
    # league-table views (but still appears, with their count, in the
    # unranked listing -- suppressed from rankings, never from existence).
    # 10 is arbitrary but non-zero on purpose: a bare Settings() must not
    # silently produce n=1 "rankings". Inert while csat_by_agent_enabled is
    # off.
    csat_ranking_min_samples: int = 10
    # Task 7: adds a `channel` dimension to QA records and, for calls, the
    # five-criterion rubric (greeting, identification, resolution, closing,
    # compliance) scored as a percentage against P5's targets-store 85%
    # value. Off = today's channel-agnostic manual qa_labels mechanism,
    # unchanged; v_quality is unaffected. Manual only, deliberately -- the
    # phone transcript path has never run against a real Twilio call, so
    # automated scoring on it would be confident noise (see
    # docs/testing/phone-channel-package-c-verification.md).
    call_qa_enabled: bool = False

    # --- P9: notification & alerting UX --------------------------------------
    # All default-off/default-inert, so a tenant that sets none of these gets
    # byte-identical behaviour to before P9: no new alert surface, no hourly
    # detector, no freshness banner.
    #
    # Task 2/3. **This setting does not currently gate anything.** The alert
    # module ships in fork patch 0057 and the only switch its client reads is
    # `hasFeature('inbound_alerts')`, from the fork's PROTON_FEATURES list; this
    # setting does not populate that and no endpoint reports it to the frontend,
    # so the two are independent switches. Same gap as
    # faq_suggestion_popup_enabled -- blocked-work register 3g/3h. Kept because
    # unifying them is the recorded fix (it needs main.py + tenant env +
    # compose), and deleting it would lose the only backend-side record that the
    # feature exists.
    #
    # What the module does once PROTON_FEATURES enables it: moves the three
    # working alert primitives (toast, sound, desktop notification) out of the
    # my-tasks iframe and into the Chatwoot fork, subscribed to the ActionCable
    # stream the SPA already uses. my-tasks keeps its SLA alerts either way --
    # this is an ADDITION, and breaking my-tasks would trade a working alert
    # surface for a new one.
    inbound_alerts_enabled: bool = False
    # Task 1/6: the per-agent alert-rule store and the preferences UI behind it.
    # Off = no rules exist and no preferences page is mounted, so every agent
    # gets the built-in defaults.
    alert_rules_enabled: bool = False
    # Task 4: the hourly channel-volume anomaly detector.
    #
    # Two properties are load-bearing and easy to "simplify" into uselessness.
    # First, the baseline is SAME-HOUR-ACROSS-DAYS (this Tuesday 14:00 against
    # recent 14:00s), never trailing hours -- a trailing-hours baseline flags
    # every lunchtime dip as an anomaly. Second, the minimum-volume floor below
    # is mandatory, not a refinement: without it the detector alerts every night
    # at 03:00, when two messages instead of the usual one is a 100% deviation.
    anomaly_hourly_enabled: bool = False
    # Deliberately LOOSER than the daily detector's anomaly_zscore_k (3.0):
    # hourly buckets are smaller and noisier, so reusing the daily threshold
    # would produce alerts nobody can act on. Tuned for signal an operator will
    # actually respond to rather than for statistical purity.
    anomaly_hourly_zscore_k: float = 3.5
    # The mandatory floor. An hour with fewer than this many conversations is
    # never flagged, however large its deviation looks -- see anomaly_hourly_
    # enabled above for why 03:00 makes this non-optional. Lower than the daily
    # anomaly_min_baseline (20) because an hour legitimately carries far less
    # volume than a day.
    anomaly_hourly_min_baseline: int = 5
    # Task 5: the shared as-of/source freshness contract on every report page.
    # Off = pages render exactly as today, with no freshness line. On, a page
    # states what its numbers are as-of and where they came from: alerting and
    # the anomaly page are live, while BigQuery-backed dashboards are a batch
    # sync (metrics_sync_interval_hours, default 6) and say so. The point is
    # that a difference between a dashboard figure and the live CRM is EXPECTED,
    # and its size becomes visible instead of being read as a bug.
    dashboard_freshness_enabled: bool = False

    # --- P10: self-service taxonomy admin, category->department, data-scoped
    # RBAC -------------------------------------------------------------------
    # Three settings, all default-off, so a tenant that sets none of them is
    # byte-identical to today: no taxonomy admin store is consulted (the
    # CASE_TAXONOMY_JSON/CASE_TYPE_OPTIONS_JSON/CASE_DETAIL_OPTIONS_JSON env
    # vars stay the live source, exactly as before this package), no
    # department is ever suggested from a category, and every role resolves
    # to the account-wide scope it has always had.
    #
    # Task 1/2/3/4: mounts the Firestore-backed taxonomy store's admin CRUD
    # endpoints (features/taxonomy/router.py) and the Chatwoot
    # attribute-definition sync that removes the "restart to add a category"
    # step. Off = those endpoints 404 (so the fork's admin page does not
    # render) and CASE_TAXONOMY_JSON/CASE_TYPE_OPTIONS_JSON/
    # CASE_DETAIL_OPTIONS_JSON remain the only source, read exactly as before
    # -- this flag does not stop them being read.
    #
    # ON DOES NOT MEAN "SWITCH SOURCE": once a tenant's store has been seeded
    # (task 2's seed.py, non-destructive), those three env vars become the
    # SEED only -- editing them afterwards has no further effect. An operator
    # edits the taxonomy through the admin endpoints/page instead.
    #
    # RETIRE, NEVER DELETE. There is deliberately no delete method on the
    # store (see features/taxonomy/store.py) -- a category attached to
    # historical cases must stay resolvable forever, or reports grouped by
    # category silently lose rows and a case's own category field stops
    # resolving. "Retire" hides a node from the active tree/picker and keeps
    # it readable by key. This is a constraint a later "simplification" could
    # easily remove by re-adding delete; don't.
    #
    # THE STORE IS AUTHORITATIVE; THE CHATWOOT SYNC IS DOWNSTREAM. A write
    # lands in the store first and always succeeds or fails atomically on its
    # own; the push into Chatwoot's custom-attribute definition happens after,
    # and if IT fails the store is NOT rolled back -- the operator's edit is
    # never silently discarded by a transient Chatwoot API error. A failed
    # sync instead surfaces an explicit out-of-sync/retry state. Do not
    # "simplify" this into a single transactional write that undoes the store
    # change on sync failure -- that trades a visible retry for a silent data
    # loss.
    taxonomy_admin_enabled: bool = False
    # Task 5: gates GET /admin/taxonomy/coverage (active categories with no
    # mapped department; dept_* slugs no category maps to). Off = that endpoint
    # 404s. Also requires taxonomy_admin_enabled, since the store is what the
    # report reads. `taxonomy/router.py::_check_coverage_enabled` is the
    # consumer; test_p10_wiring.py drives both states through the real app.
    #
    # WHAT THIS DOES NOT DO YET: `TaxonomyNode.department` exists and the
    # coverage report reads it, but nothing suggests a dept_<slug> label to an
    # agent from it -- the escalation suggestion path was never wired, so
    # category and escalation-department remain the two disconnected taxonomies
    # they are today. The half that shipped is the operationally valuable half:
    # the report that makes a correctly-categorised-but-unroutable case visible
    # before it fails silently at escalation time.
    #
    # When the suggestion is built it must be SUGGEST-ONLY, matching the
    # existing AI-suggested escalation department (dept_suggestion.py, da6c335):
    # pre-fill a value the agent can override, and record the override.
    # Auto-applying it would misroute exactly the exceptional cases that get
    # escalated -- the ones where the category is right and the department
    # genuinely isn't the mapped default. Never wire this to apply the label
    # without a human confirming it.
    category_department_mapping_enabled: bool = False
    # Task 6/7. **THIS SETTING DOES NOT CURRENTLY GATE ANYTHING.** No code
    # reads it -- not `features/authz/data_scope.py`, which never takes a
    # `Settings` at all -- so flipping it on has no effect whatsoever.
    #
    # The intent was: resolve each caller's roles to a DataScope (inboxes /
    # teams / dealers / own_only) and enforce it in the metrics and admin query
    # layers (composing with P4's MetricFilters) plus Chatwoot-native inbox
    # membership via chatwoot_role_mirror.py. Task 6's intersection logic
    # shipped and is genuinely proven by test_data_scope.py; task 7's
    # enforcement did not. `apply_scope_to_filters` and
    # `resolve_user_data_scope` have no callers, there is no FastAPI dependency,
    # role scopes live in a module-level dict with no admin surface, and
    # query_adapter.py / chatwoot_role_mirror.py were never modified. Kept
    # rather than deleted because wiring the enforcement is the recorded fix and
    # this is the only backend-side record that the scope model exists; see
    # data_scope.py's docstring and the blocked-work register.
    #
    # Everything below is the contract enforcement must honour when it lands,
    # not a description of current behaviour.
    #
    # `None` ON EVERY SCOPE FIELD MEANS ACCOUNT-WIDE, so every role that
    # exists before this package keeps behaving exactly as it does today even
    # once this is flipped on -- turning this on never narrows or widens an
    # existing role that was never given a scope.
    #
    # SCOPES ARE INTERSECTIVE, NEVER ADDITIVE. Two scopes on one caller
    # (e.g. two roles) narrow to their intersection, never their union, and
    # an EMPTY intersection means access to NOTHING, not to everything --
    # this is the security-relevant half of this package: a bug here is data
    # exposure (a caller sees rows they should not), not a reporting
    # inconvenience. Enforced in the query layer, never by hiding UI, so a
    # caller hitting the API directly (bypassing any front end) is scoped
    # exactly the same as one going through it. Do not "simplify" an empty
    # intersection into "no filter" -- that grants everything to precisely
    # the most restricted caller.
    data_scoped_rbac_enabled: bool = False

    # --- P11: voice partials (DTMF menu, after-hours, voicemail, retention) ---
    # Six default-off and one deliberately default-ON. NOTE UP FRONT: no real
    # Twilio call has ever reached any of this code, so nothing in this block may
    # be reported as MET on the strength of the suite alone -- see
    # docs/testing/phone-channel-package-c-verification.md and the blocked-work
    # register. Three of the seven currently have no consumer; each says so.
    #
    # Gates GET /calls/{conversation_id}/recording
    # (features/chat/phone/recording_router.py, mounted in main.py, permission
    # call_recording.listen). Off = 404 for a permitted caller. Read that
    # module's docstring before describing this to a client: the handler reads an
    # in-process registry nothing writes, logs rather than writing an audit-log
    # row, and returns a placeholder rather than a cryptographically signed URL.
    call_recording_retrieval_enabled: bool = False
    # **DOES NOT CURRENTLY GATE ANYTHING.** `dtmf_menu.py` exists and its prompts
    # are verbatim from deploy/twilio/ivr-studio-flow.json, but `twiml.py` was
    # never modified, so no <Gather> is emitted on any call path and nothing reads
    # this flag -- `build_dtmf_twiml(enabled=...)` takes the switch as an argument
    # no caller supplies. Turning this on has no effect.
    phone_dtmf_menu_enabled: bool = False
    # Read by `after_hours.py::evaluate_after_hours_call`, which is itself called
    # by nothing: the after-hours branch is not wired into the call path. Off is
    # therefore indistinguishable from on today. The flag has a consumer; the
    # consumer has no caller.
    phone_after_hours_enabled: bool = False
    # DEFAULT ON -- the only default-on flag in this programme, and deliberately
    # so. §8.1.6 requires roadside assistance 24/7, so when the after-hours path
    # does run, an out-of-hours RSA caller must reach help rather than a
    # closed-for-the-day message: a stranded motorist at 2 a.m. hitting a
    # voicemail box is the one failure in this package with a safety dimension
    # rather than a reporting one. It takes effect only in combination with
    # phone_after_hours_enabled. Turning it OFF is a deliberate act and is logged
    # as one (`after_hours_bypass_disabled_by_config`). Do not "tidy" this to
    # False for consistency with its neighbours.
    phone_rsa_after_hours_bypass: bool = True
    # Read by `voicemail_ingest.py::process_voicemail_webhook`, which has no
    # caller: there is no Twilio RecordingUrl webhook route, and the function
    # creates no Chatwoot conversation even when called. See its docstring --
    # notably that attend_after is now+12h, not P1's next_working_instant.
    phone_voicemail_ingest_enabled: bool = False
    # **DOES NOT CURRENTLY GATE ANYTHING.** Task 6 (per-utterance transcript
    # flush when a human agent is on the call, vs today's fifteen-second cadence
    # for an AI-only call) was not implemented: `transcript_sink.py` is unchanged
    # and no code reads this. Note the existing, separate
    # `phone_transcript_live_enabled` is what actually controls today's live
    # transcript -- do not confuse the two.
    phone_live_transcript_enabled: bool = False
    # Read by `retention.py::run_retention_purge_job`, which has no scheduler:
    # nothing invokes the job, so PHONE_RECORDING_RETENTION_DAYS still enforces
    # nothing in a running deployment. The job itself is correct and tested; it
    # needs a caller. Until then the declared retention policy remains a written
    # commitment the system does not keep.
    phone_retention_job_enabled: bool = False

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
