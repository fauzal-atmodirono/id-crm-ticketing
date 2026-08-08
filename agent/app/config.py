"""Application settings, sourced from environment variables.

Field names below map (case-insensitively) to the env vars documented in the
"Agent service" section of `deploy/.env.example` — names must match verbatim.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Internal service URL — used for all API calls (resolve via the
    # platform docker network)
    chatwoot_url: str

    # Public URL — used only for human-facing links embedded in notes.
    # Optional: falls back to the internal URL above so nothing breaks if
    # unset (links just won't be clickable from outside the docker network).
    chatwoot_public_url: str | None = None

    # Chatwoot
    chatwoot_api_token: str
    chatwoot_platform_token: str
    chatwoot_account_id: int
    chatwoot_webhook_secret: str
    chatwoot_bot_secret: str
    chatwoot_bot_token: str

    # Customer-facing acknowledgment posted (publicly) before reopening on a
    # handoff/escalation when the assistant has no persona handoff_message.
    # Empty by default (behaviour-preserving); set per tenant so a handoff
    # always acknowledges the customer instead of going silent.
    handoff_default_message: str = ""

    # Gemini / AI behavior.
    # Auth is via Application Default Credentials (Vertex AI) by default —
    # google_genai_use_vertexai=True uses the mounted ADC (GOOGLE_APPLICATION_
    # CREDENTIALS) with vertex_project_id/location; no API key needed. Set
    # google_genai_use_vertexai=False + gemini_api_key to use the AI-Studio path.
    gemini_api_key: str = ""
    google_genai_use_vertexai: bool = True
    vertex_project_id: str = ""
    vertex_location: str = "us-central1"
    gemini_model: str = "gemini-2.5-flash"
    agent_mode: str = "suggest"
    kb_grounded_replies: bool = False
    auto_resolve: bool = False
    # Route the agent-bot's decision through the backend's full ADK conversational
    # agent (POST /chat/turn) instead of the local 3-way gemini.decide router.
    # When True, the bot answers KB/spec questions itself and only hands off on
    # genuine intent (help request, negative sentiment, sales lead, KB miss) —
    # matching the Proton website agent. Default False = legacy router (byte-
    # identical). Supersedes kb_grounded_replies on this path when enabled.
    chat_agent_enabled: bool = False
    # Voice-note + image understanding for the agent-bot's chat-agent path
    # (orchestrator.py's _process_via_chat_agent, chat_agent_enabled=True
    # only). Default False = today's text-only behavior, byte-identical.
    # When True, incoming WhatsApp attachments (audio/image) are downloaded
    # and forwarded to backend/'s /chat/turn as multimodal Parts alongside
    # the text.
    whatsapp_media_understanding_enabled: bool = False
    # Inline-media budget for one turn, in RAW (pre-encode) bytes. Applied
    # BOTH to a single video and to the turn's combined audio+image+video
    # payload; anything over it is dropped (logged) and the turn proceeds on
    # what remains. google-genai ships inline_data as base64 inside a JSON
    # REST body, which inflates the payload by roughly 1.335x, and Gemini caps
    # an inline request at roughly 20 MB — so 14 MB raw encodes to ~18.7 MB
    # and still leaves headroom for the conversation history and the system
    # instruction. (Do NOT raise this to WhatsApp's own 16 MB inbound cap:
    # 16 MB encodes to ~21.4 MB and Gemini rejects the request, which is
    # exactly what this guard exists to prevent. A handful of legitimate
    # 14-16 MB clips are refused early, with a log, instead.)
    whatsapp_video_max_bytes: int = 14 * 1024 * 1024

    # Conversation lifecycle & auto-close (feature is a no-op when disabled).
    # Drives the Proton process-flow SOP: idle warn/close, resolution
    # confirmation, rating surveys, AI disclaimer. See
    # docs/superpowers/specs/2026-07-23-conversation-lifecycle-autoclose-design.md
    lifecycle_enabled: bool = False
    lifecycle_scan_interval_seconds: int = 60
    lifecycle_idle_warn_minutes: int = 10
    lifecycle_idle_close_grace_minutes: int = 5
    lifecycle_idle_close_out_of_hours_grace_minutes: int = 0
    lifecycle_confirm_grace_minutes: int = 10
    # Auto-resolve a handed-off (assigned) conversation once it has been idle
    # this many minutes, so an abandoned handoff self-clears instead of
    # swallowing the customer's later messages (which funnel into the still-open
    # thread while the bot stays silent). 0 = disabled: assigned conversations
    # are left entirely to the human (today's behavior).
    lifecycle_assigned_idle_resolve_minutes: int = 0
    lifecycle_survey_enabled: bool = True
    lifecycle_disclaimer_enabled: bool = True
    lifecycle_auto_categorize: bool = False

    # EM-7: two-thread email escalation for natively-escalated Email-channel
    # conversations. Requires PROTON_BACKEND_URL/KEY to be set (fail-open,
    # no-op otherwise). Default off, byte-identical when unset.
    email_escalation_enabled: bool = False

    # Link an emailed reply (dealer/PIC/customer) back onto the conversation
    # it was escalated from. Requires the backend's
    # ESCALATION_REPLY_TO_TEMPLATE to be set, or no mail carries a token.
    escalation_reply_linking_enabled: bool = False
    # Post an AI-drafted customer reply as a second private note alongside a
    # linked internal reply. Never sends anything to the customer.
    escalation_reply_draft_enabled: bool = False

    # Suggest-only AI escalation-department nudge (app/services/dept_suggestion.py):
    # on an incoming customer message to an Email-channel conversation with no
    # dept_* label yet, classify it against the departments that actually have
    # a PIC configured (backend GET /escalation/departments) and post a private
    # note naming one. Never applies the label itself -- a human still has to
    # add dept_<slug> before escalate. Default off, byte-identical when unset.
    dept_suggestion_enabled: bool = False

    # SOP completion (B: categorization taxonomy; C1: email auto-ack).
    # Comma-separated category slugs the bot may assign on resolution (must
    # match the tenant's deployed taxonomy). Empty → auto-categorize no-ops.
    # DEPRECATED: superseded by case_taxonomy_json; kept for backward
    # compatibility so old deployments that set this don't hard-fail on startup.
    lifecycle_category_labels: str = ""

    # Case category/subcategory taxonomy — set to the SAME value as backend/'s
    # CASE_TAXONOMY_JSON (both services parse it independently; there is no
    # shared library between them). Used by services/categorize.py as the
    # resolution-time fallback classifier's candidate list. Ships with the
    # SAME working default as backend/'s Settings.case_taxonomy_json so a
    # tenant that turns on lifecycle_auto_categorize without overriding this
    # var gets sensible behavior instead of a silent no-op; override per
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

    # Vehicle-model / case-type dimensions — SAME values as backend/'s
    # VEHICLE_MODELS_JSON / CASE_TYPE_OPTIONS_JSON (each service parses
    # independently). Used by services/categorize.py's fallback classifier.
    vehicle_models_json: str = '{"options": ["e.MAS 5", "e.MAS 7", "e.MAS 7 PHEV", "Not Applicable"]}'
    case_type_options_json: str = '{"options": ["Inquiry", "Complaint", "Feedback"]}'

    # C1: email once-per-thread auto-acknowledgement.
    email_autoack_enabled: bool = False
    email_autoack_template: str = (
        "Dear Customer,\n"
        "Thank you for your email. This message serves to acknowledge receipt "
        "of your enquiry.\n"
        "We will respond within one (1) business day during our operating hours.\n"
        "For urgent matters, please contact our Call Centre at 1300 888 877.\n"
        "Operating Hours:\n"
        "Monday–Friday: 8:30 AM – 5:30 PM\n"
        "Saturday, Sunday & Public Holidays: 9:00 AM – 5:00 PM\n"
        "Thank you for your patience and understanding.\n\n"
        "Warm regards,\n"
        "Proton e.MAS Centre"
    )

    # Agent service's own database
    agent_database_url: str

    # Proton conversational-AI backend (optional; feature disabled when blank)
    proton_backend_url: str | None = None
    proton_backend_key: str | None = None

    @property
    def chatwoot_display_url(self) -> str:
        """Chatwoot base URL for human-facing links: public if configured,
        otherwise the internal URL (still valid inside the docker network,
        just not clickable from outside it)."""
        return self.chatwoot_public_url or self.chatwoot_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
