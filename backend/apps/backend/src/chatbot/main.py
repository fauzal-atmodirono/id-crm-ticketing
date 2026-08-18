from __future__ import annotations

import os

import structlog
from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from chatbot.features.assist.chatwoot_context import ChatwootContextClient
from chatbot.features.assist.copilot_router import build_copilot_router
from chatbot.features.assist.router import build_assist_router
from chatbot.features.assist.translate_router import build_translate_router
from chatbot.features.authz.audit_purge import build_audit_row_source, start_audit_purge_job
from chatbot.features.chat.adapters.assistants_store import build_assistants_store
from chatbot.features.chat.adapters.audit_log import build_audit_log
from chatbot.features.chat.adapters.bigquery_metrics import build_metrics_port
from chatbot.features.chat.adapters.chatwoot import ChatwootAdapter
from chatbot.features.chat.adapters.gcp_voice import GeminiTextToSpeechAdapter
from chatbot.features.chat.adapters.handoff_store import build_handoff_store
from chatbot.features.chat.adapters.inbox_assignment_store import build_inbox_assignment_store
from chatbot.features.chat.adapters.inbox_timing_store import build_inbox_timing_store
from chatbot.features.chat.adapters.live_faq import VertexEmbedder, build_live_faq_store
from chatbot.features.chat.adapters.mock import InMemoryKnowledgeAdapter, MockVoiceAdapter
from chatbot.features.chat.adapters.noop_conversation_log import NoOpConversationLog
from chatbot.features.chat.adapters.scenarios_store import build_scenarios_store
from chatbot.features.chat.adapters.sunshine_conversations import SunshineConversationsAdapter
from chatbot.features.chat.adapters.tenant_settings_store import build_tenant_settings_store
from chatbot.features.chat.adapters.tools_store import build_tools_store
from chatbot.features.chat.adapters.twilio_channel import TwilioChannelAdapter
from chatbot.features.chat.adapters.vertex_search import VertexAISearchAdapter
from chatbot.features.chat.adapters.zendesk import ZendeskAdapter
from chatbot.features.chat.dms_client import DmsClient, MockDmsClient
from chatbot.features.chat.dms_config_store import DmsConfigStore
from chatbot.features.chat.escalation_attachments import ChatwootAttachmentFetcher
from chatbot.features.chat.escalation_notifier import EscalationNotifier, build_dealer_email_map
from chatbot.features.chat.escalation_router import build_escalation_router
from chatbot.features.chat.faq_admin_router import build_faq_admin_router
from chatbot.features.chat.handoff_bridge import HandoffBridge
from chatbot.features.chat.kb_assistants_router import build_kb_assistants_router
from chatbot.features.chat.kb_db import build_engine as build_kb_engine
from chatbot.features.chat.kb_db import build_session_maker as build_kb_session_maker
from chatbot.features.chat.kb_documents_router import build_kb_documents_router
from chatbot.features.chat.kb_inboxes_router import build_kb_inboxes_router
from chatbot.features.chat.kb_scenarios_router import build_kb_scenarios_router
from chatbot.features.chat.kb_settings_router import build_kb_settings_router
from chatbot.features.chat.kb_suggest_router import build_kb_suggest_router
from chatbot.features.chat.kb_tools_router import build_kb_tools_router
from chatbot.features.chat.phone.handoff_target import validate_handoff_target_settings
from chatbot.features.chat.phone.retention import start_recording_retention_job
from chatbot.features.chat.pic_registry import build_pic_registry
from chatbot.features.chat.pic_store import DealerStore, PicStore
from chatbot.features.chat.ports import (
    ChatPort,
    ConversationLogPort,
    HumanAgentBridgePort,
    KnowledgePort,
    TextToSpeechPort,
    TicketingPort,
)
from chatbot.features.chat.resolved_case_adapters import (
    AssistSummarizeAdapter,
    ChatwootTranscriptAdapter,
    find_summarize_endpoint,
)
from chatbot.features.chat.resolved_case_index import (
    PgResolvedCaseRepository,
    ResolvedCaseIndexer,
    init_resolved_case_index_db,
)
from chatbot.features.chat.router import build_chat_router
from chatbot.features.chat.service import OrchestratorService
from chatbot.features.chat.sla import start_sla_scheduler
from chatbot.features.health_enrichment import build_health_router
from chatbot.features.metrics.anomaly_router import build_metrics_anomaly_router
from chatbot.features.metrics.dashboard_router import build_metrics_query_router
from chatbot.features.metrics.email_port import build_email_report_port
from chatbot.features.metrics.email_sender import SmtpEmailSender
from chatbot.features.metrics.export_router import build_metrics_export_router
from chatbot.features.metrics.faq_feedback_adapter import build_faq_feedback_port
from chatbot.features.metrics.faq_router import build_faq_router
from chatbot.features.metrics.insights_router import build_metrics_insights_router
from chatbot.features.metrics.qa_adapter import build_qa_label_port
from chatbot.features.metrics.qa_router import build_qa_router
from chatbot.features.metrics.query_adapter import build_metrics_query_port
from chatbot.features.metrics.scheduler import start_metrics_scheduler, start_report_scheduler
from chatbot.features.routing.acw import build_acw_controller, start_acw_sweeper
from chatbot.features.routing.assigner import RoutingAssigner
from chatbot.features.routing.custom_status import build_custom_status_store
from chatbot.features.routing.presence import PresenceFetcher
from chatbot.features.routing.presence_poller import start_presence_poller
from chatbot.features.routing.presence_store import build_presence_event_store
from chatbot.features.routing.presence_thresholds import start_presence_threshold_sweeper
from chatbot.features.routing.router import build_routing_router
from chatbot.features.routing.service import RoutingService
from chatbot.features.routing.status_router import build_status_router
from chatbot.features.routing.store import ChannelPriorityStore
from chatbot.features.routing.sweeper import start_routing_sweeper
from chatbot.features.routing.workforce_router import build_workforce_router
from chatbot.features.tasks.tasks_router import build_tasks_router
from chatbot.platform.config import Settings, get_settings
from chatbot.platform.logger import configure_logging
from chatbot.platform.metered_genai import (
    SURFACE_ASSIST_COPILOT,
    SURFACE_ASSIST_SUGGEST,
    SURFACE_ASSIST_TRANSLATE,
    SURFACE_EMBED,
    build_metered_genai_client,
    with_surface,
)
from chatbot.platform.server import create_app

# Module-level logger. The lazy `import structlog as _sl` blocks elsewhere in
# this file predate it and are left alone; new wiring uses this.
_log = structlog.get_logger(__name__)


def _build_genai_client(settings: Settings) -> object | None:
    """Build a google-genai client (ADC / Vertex), **routed through the token
    metering wrapper**. Returns None if the SDK/credentials are unavailable so
    live-FAQ wiring falls back to Vertex-Search-only suggestions and boot never
    breaks -- unchanged from before metering.

    This one function feeds several subsystems, so three things about its new
    behaviour are load-bearing for anyone editing this file:

    1. **The returned object is not always a `google.genai.Client`.** With
       `token_metering_enabled` on it is a `MeteredGenaiClient` proxy that
       forwards every attribute it does not intercept. With the flag off
       (the default) it is the raw SDK client *by identity* -- no proxy exists,
       so the model path is byte-identical to pre-P8 with no added latency and
       no extra I/O. Nothing downstream may `isinstance`-check the SDK type.
    2. **The surface label is applied at hand-over, not here.** Every consumer
       of this function shares one client, and the cost report groups spend by
       surface, so a single construction-time label would flatten three assist
       products and the embedder into one indistinguishable line. The base
       label is therefore `embed` -- correct for the majority of consumers,
       all of which are `VertexEmbedder`/live-FAQ -- and each generative
       consumer re-labels its own view with `with_surface(...)` **at the point
       the client is handed to it**. `with_surface` returns a view over the
       *same* SDK client (no second connection) and is a no-op on an unmetered
       client, so no call site needs a flag branch. Order matters only in that
       direction: re-label at hand-over, never mutate the shared client.
    3. **Ordering/cost at boot.** With the flag on, each call here also builds
       its own `TokenUsageSink`; for a tenant on the BigQuery metrics provider
       that means one idempotent `create_table(exists_ok=True)` per call. This
       mirrors the pre-existing structure of this file, which already builds a
       separate raw client per wiring block; consolidating to a single shared
       client (and sink) is a worthwhile but separate refactor. With the flag
       off no sink is built at all -- the flag is checked before the sink is
       constructed.
    """
    return build_metered_genai_client(settings, surface=SURFACE_EMBED)


def _wire_assist(
    app: FastAPI,
    knowledge_port: KnowledgePort,
    settings: Settings,
    assistants_store: object | None = None,
    tenant_settings_store: object | None = None,
) -> APIRouter:
    """Wire the three /assist/* endpoints and add Chatwoot origins to CORS.

    Returns the built router. The caller needs the router object itself, not
    just the mount: P7's resolved-case summariser runs `/assist/summarize`'s own
    endpoint function in-process rather than standing up a second summarisation
    prompt, and `find_summarize_endpoint` locates that function on this object.
    See `features/chat/resolved_case_adapters.py` for why the alternative --
    extracting the closure out of `features/assist/router.py` -- was rejected.
    """
    genai_client = _build_genai_client(settings)
    assist_router = build_assist_router(
        settings,
        knowledge_port,
        # /assist/suggest, /assist/summarize and /assist/ask share this router
        # and therefore this one label; the router does not hand its client on
        # per endpoint. Their spend rolls up under `assist.suggest`, which
        # includes P7's resolved-case summariser (it runs /assist/summarize's
        # own endpoint function in-process).
        with_surface(genai_client, SURFACE_ASSIST_SUGGEST),
        assistants_store=assistants_store,  # type: ignore[arg-type]
        tenant_settings_store=tenant_settings_store,  # type: ignore[arg-type]
        # Read-only Chatwoot client the media path uses to look up the
        # conversation's attachments. Always wired: the endpoints gate on
        # `assist_media_understanding_enabled` themselves and make no Chatwoot
        # call at all when it is off, so passing the client costs nothing and
        # keeps the flag as the single switch.
        chatwoot_context=ChatwootContextClient(settings),
    )
    app.include_router(assist_router)

    # Extend the existing CORS middleware to allow Chatwoot origins to call
    # /assist/* cross-origin. The CORSMiddleware is already added with
    # settings.frontend_origins; append the assist_cors_origins here by
    # adding a second, narrower middleware that covers only /assist/*.
    # (Simpler: just add assist_cors_origins to frontend_origins in the tenant env
    # and rely on the single middleware. Use this note if the two-middleware
    # approach adds complexity.)
    # For Phase 0, the operator adds Chatwoot URLs to ASSIST_CORS_ORIGINS in the
    # backend's .env, and they are merged into allow_origins at startup:
    if settings.assist_cors_origins:
        import structlog as _sl

        _sl.get_logger(__name__).info(
            "assist_cors_origins_added", count=len(settings.assist_cors_origins)
        )
        # The CORSMiddleware registered above already covers frontend_origins.
        # Re-registering with a merged list is the simplest approach; FastAPI
        # evaluates middlewares in stack order and the first matching one wins.
        from fastapi.middleware.cors import CORSMiddleware as _CORS

        app.add_middleware(
            _CORS,
            allow_origins=settings.assist_cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            # The three x-chatwoot-* headers carry the caller's devise_token_auth
            # session, which /assist/translate needs: it is the one /assist/*
            # endpoint gated per-user by require_permission rather than by the
            # shared secret. Omitting them here fails the browser's PREFLIGHT
            # ("Disallowed CORS headers"), i.e. before the request ever reaches
            # the dependency — a different and more confusing failure than the
            # 401 the missing headers themselves produce. This middleware is not
            # actually path-scoped despite the comment above, so the same three
            # are what protonAdmin.js's adminRequest sends to /admin/* too.
            allow_headers=[
                "x-api-key",
                "content-type",
                "x-chatwoot-access-token",
                "x-chatwoot-client",
                "x-chatwoot-uid",
            ],
        )

    return assist_router


def _wire_copilot(
    app: FastAPI,
    knowledge_port: KnowledgePort,
    settings: Settings,
    assistants_store: object,
    tenant_settings_store: object,
    tools_store: object,
    scenarios_store: object,
    assignment_store: object,
) -> None:
    """Wire POST /assist/copilot (Ask Copilot). Receives shared store instances."""
    genai_client = _build_genai_client(settings)
    app.include_router(
        build_copilot_router(
            settings,
            knowledge_port,
            with_surface(genai_client, SURFACE_ASSIST_COPILOT),
            assistants_store,  # type: ignore[arg-type]
            tenant_settings_store,  # type: ignore[arg-type]
            tools_store=tools_store,  # type: ignore[arg-type]
            scenarios_store=scenarios_store,  # type: ignore[arg-type]
            assignment_store=assignment_store,  # type: ignore[arg-type]
        )
    )


def _wire_agent_assist(
    app: FastAPI,
    knowledge_port: KnowledgePort,
    settings: Settings,
    assistants_store: object,
    tenant_settings_store: object,
    tools_store: object,
    scenarios_store: object,
    assignment_store: object,
    timing_store: object,
) -> None:
    """Wire the agent-assist FAQ routers: kb-suggest (Vertex Search + live FAQ
    semantic merge), faq-feedback, and the real-time FAQ admin CRUD.
    Receives shared store instances so copilot and agent-assist see the same data."""
    genai_client = _build_genai_client(settings)
    live_faq_store = build_live_faq_store(settings, genai_client)  # type: ignore[arg-type]
    embedder = (
        VertexEmbedder(genai_client, settings.embedding_model)  # type: ignore[arg-type]
        if genai_client is not None
        else None
    )

    app.include_router(build_kb_suggest_router(knowledge_port, live_faq_store, embedder))
    app.include_router(build_faq_admin_router(live_faq_store, settings))
    app.include_router(build_kb_documents_router(settings))
    app.include_router(
        build_kb_assistants_router(
            assistants_store,  # type: ignore[arg-type]
            settings,
            scenarios_store=scenarios_store,  # type: ignore[arg-type]
            assignment_store=assignment_store,  # type: ignore[arg-type]
        )
    )
    app.include_router(build_kb_settings_router(tenant_settings_store, settings))  # type: ignore[arg-type]

    app.include_router(build_kb_tools_router(tools_store, settings))  # type: ignore[arg-type]

    app.include_router(build_kb_scenarios_router(scenarios_store, tools_store, settings))  # type: ignore[arg-type]

    # Inbox assignment router: ChatwootAdapter for listing inboxes (best-effort).
    chatwoot_adapter_for_inboxes = ChatwootAdapter(settings)
    app.include_router(
        build_kb_inboxes_router(
            assignment_store,  # type: ignore[arg-type]
            assistants_store,  # type: ignore[arg-type]
            tenant_settings_store,  # type: ignore[arg-type]
            chatwoot_adapter_for_inboxes,
            settings,
            timing_store,  # type: ignore[arg-type]
        )
    )

    faq_port = build_faq_feedback_port(settings)
    app.include_router(build_faq_router(faq_port, settings))


def _wire_metrics_features(app: FastAPI, settings: Settings) -> None:
    """Wire QA-labelling router, dashboard read API, and metrics-scheduler shutdown."""
    qa_port = build_qa_label_port(settings)
    app.include_router(build_qa_router(qa_port, settings))

    query_port = build_metrics_query_port(settings)
    # `settings` (P9 task 7): the §2.2.3 executive dashboard was the one metrics
    # response with no freshness stamp, because this factory took no Settings.
    # It reads the same BigQuery views the reporting endpoints do, so an unstamped
    # figure here is the one most likely to be quoted as live in a meeting.
    app.include_router(build_metrics_query_router(query_port, settings))
    app.include_router(build_metrics_export_router(query_port, settings))
    app.include_router(build_metrics_anomaly_router(query_port, settings))
    app.include_router(build_metrics_insights_router(query_port, settings))

    # P5: the fourteen-row control-item slide. The actuals provider is
    # deliberately not wired yet -- every row then reports `no_data` with its
    # reason, which is the honest state until the per-row queries land, and is
    # never rendered as a missed target.
    from chatbot.features.metrics.control_items_router import build_control_items_router
    from chatbot.features.metrics.targets_store import build_targets_store

    app.include_router(build_control_items_router(build_targets_store(settings), settings))

    report_scheduler = start_report_scheduler(
        settings, query_port, build_email_report_port(settings)
    )
    if report_scheduler is not None:

        @app.on_event("shutdown")
        def _stop_report_scheduler() -> None:
            report_scheduler.shutdown(wait=False)

    metrics_scheduler = start_metrics_scheduler(settings)
    if metrics_scheduler is not None:

        @app.on_event("shutdown")
        def _stop_metrics_scheduler() -> None:
            metrics_scheduler.shutdown(wait=False)


def bootstrap_application() -> FastAPI:  # noqa: PLR0912, PLR0915
    """Bootstrap settings, structured logging, adapters, CORS, and routes."""
    settings = get_settings()
    configure_logging(settings.debug)

    # P11 task 5's stated constraint: "placeholder numbers must fail loudly, at
    # startup, not at dial time." This is the caller that makes that true --
    # `validate_handoff_target_settings` shipped with none, so the guard never
    # ran anywhere. It is a REFUSAL rather than a warning, deliberately:
    #
    #   - The alternative failure is silent and badly timed. A tenant
    #     provisioned from a copied env boots clean carrying e.g.
    #     `+60300000001`, and the first human handoff dials an unallocated
    #     number. The customer hears silence and the dial-status webhook
    #     records `no-answer` -- byte-identical to an agent simply not picking
    #     up, so nobody investigates the configuration. On an RSA call that is
    #     2 a.m. and a stranded motorist.
    #   - It cannot regress an existing tenant. It only fires when
    #     `phone_handoff_enabled` is true, which defaults false and which no
    #     tenant env in `deploy/tenants/` sets; and when it does fire, the
    #     handoff it refuses to boot with could not have worked anyway.
    #   - A startup ValueError is the established shape here, not a novelty:
    #     `Settings._phone_flag_dependencies` already refuses an unsafe phone
    #     flag combination the same way.
    #
    # Placed before any adapter is constructed so the message is the first
    # thing in the log rather than buried behind unrelated wiring. Logged as
    # well as raised because a container that exits on an exception may show
    # only the traceback, and the structured line is what alerting sees.
    try:
        validate_handoff_target_settings(settings)
    except ValueError as exc:
        _log.error("phone_handoff_target_invalid_refusing_to_start", error=str(exc))
        raise

    # google-genai (used by ADK under the hood) reads these from os.environ, not
    # from our Settings object. Mirror them so a single .env drives both layers.
    if settings.google_genai_use_vertexai:
        os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "true"
        if settings.vertex_project_id:
            os.environ["GOOGLE_CLOUD_PROJECT"] = settings.vertex_project_id
        os.environ["GOOGLE_CLOUD_LOCATION"] = settings.vertex_location

    app = create_app(settings)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.frontend_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=[
            "X-Reply-Text",
            "X-Handoff-Reason",
            "X-Handoff-Summary",
            "X-Handoff-Urgency",
            "X-Handoff-Language",
            "X-Handoff-Live-Chat",
            "X-User-Transcription",
            "X-Forwarded-To-Agent",
        ],
    )

    # --- CRM and Ticketing wiring ---
    chat_port: ChatPort
    ticketing_port: TicketingPort
    knowledge_port: KnowledgePort

    zendesk_client: ZendeskAdapter | None = None
    chatwoot_client: ChatwootAdapter | None = None
    human_agent_bridge: HumanAgentBridgePort | None = None
    handoff_bridge: HandoffBridge | None = None

    # --- Phase-2: PIC registry + email sender (constructed once; used by all paths) ---
    # pic_store/dealer_store are built unconditionally (regardless of RBAC status) so
    # PicRegistry/EscalationNotifier can read operator-edited routing config even when
    # RBAC — and therefore the admin CRUD router below — is off. The RBAC block reuses
    # these same instances rather than constructing duplicates.
    pic_store = PicStore(settings)
    dealer_store = DealerStore(settings)
    pic_registry = build_pic_registry(settings, store=pic_store)
    email_sender = SmtpEmailSender(settings)

    # Built here (rather than down with the other "shared stores" below) so
    # EscalationNotifier -- constructed further down, before that block --
    # can read operator-edited email templates too (Task 18). Only one
    # instance is ever built; the later block reuses this same variable.
    _shared_tenant_settings_store = build_tenant_settings_store(settings)

    # Package F: DMS/TSP integration shell config store. Built unconditionally
    # (same reasoning as pic_store/dealer_store above) so a future Customer 360
    # DMS block can read it even when RBAC — and therefore the admin CRUD
    # router below — is off; the store itself never touches Firestore until
    # a caller actually invokes get()/get_credential()/save().
    dms_config_store = DmsConfigStore(settings)

    # The client Customer 360 is allowed to call. Phase 1 ships no real DMS
    # adapter (see the package design doc), so the only thing that can ever
    # be wired here is a demo client — and that must be an explicit,
    # deliberate opt-in, never the default. Off (the default): `dms_client`
    # stays `None`, which — combined with `customer360_router.py`'s own
    # enabled/disabled check on `dms_config_store` — means MockDmsClient is
    # *never* constructed anywhere in this file unless an operator sets
    # DMS_MOCK_CLIENT_ENABLED. An operator who separately flips "enabled" in
    # the admin UI without this still correctly reads as "unreachable" (not
    # connected), never as a silent, misleadingly-empty "ok" — see
    # customer360_router.py's module docstring for why that distinction
    # matters.
    #
    # `environment=` is what makes MockDmsClient's own sandbox refusal reachable.
    # This line used to read `MockDmsClient()`, so the argument took the class's
    # own default of "sandbox" and the guard could not fire on any tenant --
    # meaning a production deployment with DMS_MOCK_CLIENT_ENABLED=true showed
    # fabricated vehicle and service records on a real customer's panel. The
    # argument is now required (no default on the class), so this cannot silently
    # regress; `app_environment` defaults to "production", i.e. to refusing.
    dms_client: DmsClient | None = (
        MockDmsClient(environment=settings.app_environment)
        if settings.dms_mock_client_enabled
        else None
    )

    # --- P6: the two presence stores (constructed unconditionally) ---
    # Same reasoning as pic_store/dealer_store/dms_config_store above: neither
    # touches Firestore until a caller actually invokes a method, so building
    # them is free even on a tenant with every P6 flag off. They are built here,
    # ahead of everything that reads them, because four separate consumers need
    # to see the SAME instances rather than four private copies: the routing
    # service's fair-share `routable` filter, the workforce dashboard, the ACW
    # controller and the startup seed below. Each of those consumers is
    # individually flag-gated; construction is not, so there is nothing to gate.
    _presence_event_store = build_presence_event_store(settings)
    _custom_status_store = build_custom_status_store(settings)

    if settings.crm_provider == "zendesk":
        zendesk_client = ZendeskAdapter(settings)
        chat_port = zendesk_client
        ticketing_port = zendesk_client
        if settings.zendesk_app_id and settings.zendesk_key_id and settings.zendesk_secret_key:
            human_agent_bridge = SunshineConversationsAdapter(settings)
            handoff_bridge = HandoffBridge(store=build_handoff_store(settings))
    else:
        # Construct without escalation_notifier first so we can pass _request into it.
        chatwoot_client = ChatwootAdapter(settings, pic_registry=pic_registry)
        # twilio_adapter is constructed later; we defer EscalationNotifier wiring to
        # after the Twilio block below.  A post-construction assignment is safe here
        # because escalation only fires inside async request handling, which occurs
        # after bootstrap_application() returns.
        chat_port = chatwoot_client
        ticketing_port = chatwoot_client
        if settings.chatwoot_enabled:
            human_agent_bridge = chatwoot_client
            handoff_bridge = HandoffBridge(store=build_handoff_store(settings))

    # --- Knowledge wiring ---
    if settings.knowledge_provider == "vertex_search":
        knowledge_port = VertexAISearchAdapter(settings)
    elif settings.knowledge_provider == "zendesk" and zendesk_client is not None:
        knowledge_port = zendesk_client
    else:
        knowledge_port = InMemoryKnowledgeAdapter()

    # --- Voice (TTS) wiring — STT is no longer a separate step; Gemini consumes audio natively ---
    tts_port: TextToSpeechPort
    if settings.voice_provider == "gcp":
        tts_port = GeminiTextToSpeechAdapter(settings)
    else:
        tts_port = MockVoiceAdapter()

    # --- Conversation capture ---
    conversation_log_port: ConversationLogPort
    if zendesk_client is not None:
        conversation_log_port = zendesk_client
    elif chatwoot_client is not None:
        conversation_log_port = chatwoot_client
    else:
        conversation_log_port = NoOpConversationLog()

    # --- Twilio channel (outbound WhatsApp) ---
    twilio_adapter: TwilioChannelAdapter | None = None
    if settings.twilio_account_sid and settings.twilio_auth_token:
        twilio_adapter = TwilioChannelAdapter(settings)

    # --- Audit log (case state transition trail) ---
    # Built before the escalation router because POST /escalation/acknowledge
    # writes to it (P1 task 6).
    audit_log = build_audit_log(settings)

    # --- Phase-2: wire EscalationNotifier into ChatwootAdapter now that twilio is ready ---
    # Post-construction injection is safe: escalation only fires inside async request
    # handlers, which run after bootstrap_application() returns.
    if chatwoot_client is not None:
        escalation_notifier = EscalationNotifier(
            settings=settings,
            pic_registry=pic_registry,
            email_sender=email_sender,
            twilio_adapter=twilio_adapter,
            # Package C Task 5 review fix (Critical 1, round 2): inject the
            # merge-safe writer, not the raw request method -- a bare POST
            # to /custom_attributes REPLACES the whole object, and
            # notify() calls _write_case_state on every escalation.
            chatwoot_request=chatwoot_client._merge_custom_attributes,
            dealer_email_map=build_dealer_email_map(settings),
            dealer_store=dealer_store,
            tenant_settings_store=_shared_tenant_settings_store,
            # P2: posts the customer acknowledgement into the thread on every
            # non-Email channel. A lambda rather than a method so the notifier
            # stays free of Chatwoot URL knowledge.
            chatwoot_post_message=lambda conv_id, payload: chatwoot_client._request(
                "POST", f"/conversations/{conv_id}/messages", payload
            ),
            # P2: carries the customer's photos/PDFs into the PIC and dealer
            # mail. Inert until escalation_attachment_budget_bytes is non-zero.
            attachment_fetcher=ChatwootAttachmentFetcher(
                chatwoot_client._request  # type: ignore[arg-type]
            ),
            # P2: one audit row per escalation leg, so "we escalated it" can be
            # checked rather than assumed.
            audit=audit_log,
            # P2: who is actually on duty. Its own instance rather than the
            # routing one below, which is constructed later; PresenceFetcher
            # holds only settings and opens a client per call, so a second one
            # costs nothing. Inert unless escalation_presence_check_enabled.
            presence=PresenceFetcher(settings),
        )
        chatwoot_client._escalation_notifier = escalation_notifier  # type: ignore[assignment]
        app.include_router(
            build_escalation_router(
                escalation_notifier,
                chatwoot_client._request,  # type: ignore[arg-type]
                settings,
                pic_store=pic_store,
                dealer_store=dealer_store,
                audit=audit_log,
            )
        )

    # --- Metrics port (per-turn BigQuery streaming) ---
    metrics_port = build_metrics_port(settings)

    # --- Shared stores (built once, passed to orchestrator, agent-assist and copilot) ---
    _shared_assistants_store = build_assistants_store(settings)
    _shared_tools_store = build_tools_store(settings)
    _shared_scenarios_store = build_scenarios_store(settings)
    _shared_assignment_store = build_inbox_assignment_store(settings)
    _shared_timing_store = build_inbox_timing_store(settings)

    orchestrator = OrchestratorService(
        settings=settings,
        chat_port=chat_port,
        ticketing_port=ticketing_port,
        knowledge_port=knowledge_port,
        tts_port=tts_port,
        human_agent_bridge=human_agent_bridge,
        handoff_bridge=handoff_bridge,
        conversation_log_port=conversation_log_port,
        metrics_port=metrics_port,
        assignment_store=_shared_assignment_store,
        assistants_store=_shared_assistants_store,
        tenant_settings_store=_shared_tenant_settings_store,
    )

    # --- P6: after-call work, built before the chat router that consumes it ---
    # RoutingAssigner used to be constructed further down, in the Phase 5 block,
    # AFTER build_chat_router. It has moved up here because the dependency chain
    # runs chat router -> ACW controller -> assigner: the phone dial-status
    # webhook lives in the chat router and is what puts an agent into ACW at
    # call end. The Phase 5 routing router below reuses this same instance
    # rather than constructing a second one.
    _routing_assigner = RoutingAssigner(settings)
    # Passed in unconditionally, not gated here: ChatRouter checks
    # `settings.acw_enabled` itself before it does any work at all (no ticket
    # lookup, no assignee resolution, no status write), so with the flag off the
    # controller is an unreachable object rather than an inert code path -- and
    # `start_acw_sweeper` below needs the same instance to sweep.
    _acw_controller = build_acw_controller(settings, _routing_assigner)

    # --- P7 task 9: auto-summary on resolve + the resolved-case index ---------
    # Built here, like the ACW controller above, because the resolve hook that
    # drives it lives in the chat router mounted immediately below.
    #
    # The summariser deliberately does NOT own a prompt. It runs
    # `/assist/summarize`'s own endpoint function in-process, so there is exactly
    # one summariser prompt in this codebase and the automatic path cannot drift
    # from the agent-triggered one -- including the PII-omission sentence the
    # index's stated mitigation rests on. That endpoint does not exist yet at
    # this point in the boot (the assist router is built much further down, after
    # the RBAC block it needs), so the route is bound to this adapter afterwards.
    # Post-construction injection is safe for the same reason the
    # EscalationNotifier assignment above is: nothing calls it until an async
    # request handler runs, long after bootstrap_application() has returned. Left
    # unbound it returns "" and the indexer stores nothing, which is the flag-off
    # behaviour rather than an error.
    _resolved_case_summarizer = AssistSummarizeAdapter(settings)
    # Reads GET /conversations/{id}/messages through the adapter's own fail-open
    # `_request`, the same injection ChatwootAttachmentFetcher takes. None on the
    # zendesk crm_provider path, which has no equivalent read: the indexer then
    # summarises an empty transcript, which the summariser declines, so nothing
    # is posted or stored.
    _resolved_case_transcript = (
        ChatwootTranscriptAdapter(chatwoot_client._request) if chatwoot_client is not None else None
    )
    _resolved_case_repo = None
    _resolved_case_embedder = None
    if settings.resolved_case_index_enabled:
        # The index is a table in the operator-KB database, so it inherits that
        # subsystem's configuration -- and `knowledge_pg_enabled` /
        # `knowledge_database_url` are independently default-off. "Index enabled,
        # KB never configured" is therefore a real operator state, not a
        # misconfiguration worth failing a boot over: log it plainly and leave
        # the repository None, which makes the index a logged no-op per resolve.
        # Resolving a case is the agent's action; our summarisation is an add-on
        # and must never be able to turn a successful resolve into an error.
        if settings.knowledge_pg_enabled and settings.knowledge_database_url:
            _resolved_genai = _build_genai_client(settings)
            _resolved_case_embedder = (
                VertexEmbedder(_resolved_genai, settings.embedding_model)  # type: ignore[arg-type]
                if _resolved_genai is not None
                else None
            )
            if _resolved_case_embedder is not None:
                # Its own engine rather than the KB block's below: that block is
                # built later and only when `knowledge_pg_enabled` is on, and a
                # second engine against the same URL is the cheaper of the two
                # ways to avoid a forward reference. The TABLE is the shared
                # state, and it has its own declarative Base -- see
                # resolved_case_index.py on why a purge here cannot reach
                # kb_documents/kb_chunks.
                _resolved_case_engine = build_kb_engine(settings.knowledge_database_url)
                _resolved_case_repo = PgResolvedCaseRepository(
                    build_kb_session_maker(_resolved_case_engine)
                )
                app.state.resolved_case_engine = _resolved_case_engine
            else:
                _log.warning(
                    "resolved_case_index_enabled_but_no_embedder",
                    detail=(
                        "RESOLVED_CASE_INDEX_ENABLED is true but the genai client is "
                        "unavailable, so summaries cannot be embedded; nothing will be "
                        "indexed. Auto-summary notes are unaffected."
                    ),
                )
        else:
            _log.warning(
                "resolved_case_index_enabled_but_kb_not_configured",
                detail=(
                    "RESOLVED_CASE_INDEX_ENABLED is true but KNOWLEDGE_PG_ENABLED / "
                    "KNOWLEDGE_DATABASE_URL are not set; the index has nowhere to "
                    "write and every resolve will log resolved_case_index_no_repository. "
                    "Auto-summary notes are unaffected."
                ),
                knowledge_pg_enabled=settings.knowledge_pg_enabled,
                knowledge_database_url_set=bool(settings.knowledge_database_url),
            )

    # Passed in unconditionally, same reasoning as the ACW controller above:
    # `handle_resolved` returns before touching a single collaborator when both
    # of its flags are off, so with them off this is an unreachable object rather
    # than an inert code path.
    _resolved_case_index = ResolvedCaseIndexer(
        settings=settings,
        ticketing_port=ticketing_port,
        summarizer=_resolved_case_summarizer,
        transcript_port=_resolved_case_transcript,
        repository=_resolved_case_repo,
        embedder=_resolved_case_embedder,
    )

    @app.on_event("startup")
    async def _init_resolved_case_db() -> None:
        engine = getattr(app.state, "resolved_case_engine", None)
        if engine is not None:
            await init_resolved_case_index_db(engine)

    # --- Task 9: agent softphone (registry + call control), built before the
    # chat router that consumes them. `SoftphoneRegistry` is Firestore-backed
    # but built lazily (no I/O at construction, see its own module docstring),
    # same convention as pic_store/dealer_store above. `CallControl` is the
    # same fail-open Twilio REST wrapper `PhoneBridge` already defaults to
    # internally; ChatRouter needs its own instance too so `_enter_acw_best_
    # effort` (Task 10) can look up who actually answered a fan-out `<Dial>`.
    # `_routing_presence` is normally built further down in the Phase 5 block
    # below -- moved up here (and NOT duplicated there) because ChatRouter's
    # own fan-out helper (`_fanout_identities`, Task 8) needs the SAME
    # instance, not a second `PresenceFetcher` with its own HTTP config.
    from chatbot.features.chat.phone.call_control import CallControl
    from chatbot.features.chat.phone.softphone_registry import SoftphoneRegistry

    softphone_registry = SoftphoneRegistry(settings)
    _call_control = CallControl(settings)
    _routing_presence = PresenceFetcher(settings)

    app.include_router(
        build_chat_router(
            orchestrator=orchestrator,
            handoff_bridge=handoff_bridge,
            human_agent_bridge=human_agent_bridge,
            twilio_adapter=twilio_adapter,
            audit_log=audit_log,
            acw_controller=_acw_controller,
            resolved_case_index=_resolved_case_index,
            softphone_registry=softphone_registry,
            presence_fetcher=_routing_presence,
            call_control=_call_control,
        )
    )

    # --- Phase 5: agent routing & presence ---
    # Only wire the RoutingService onto the live chat adapter when routing is
    # enabled. The config router itself is mounted unconditionally (its GET
    # endpoints back the routing-admin UI) -- but down in the P6 block below,
    # after RBAC, because the supervisor-reassignment path needs the authz
    # repo/validator that block builds.
    # `_routing_presence` is built above, alongside the softphone/call-control
    # wiring, and reused here rather than constructed a second time.
    _routing_priority_store = ChannelPriorityStore(settings)
    _routing_svc = RoutingService(
        presence=_routing_presence,
        store=_routing_priority_store,
        settings=settings,
        # P6 fair share: consulted only when routing_fair_share_enabled is on
        # (pick_agent skips the whole `routable` filter and the open-count fetch
        # otherwise), so wiring them in unconditionally keeps flag-off selection
        # byte-identical, including which Chatwoot calls are made.
        custom_status_store=_custom_status_store,
        presence_store=_presence_event_store,
    )
    if settings.routing_enabled and chatwoot_client is not None:
        chatwoot_client._routing_service = _routing_svc  # type: ignore[assignment]

    # --- Agent-assist FAQ ---
    _wire_agent_assist(
        app,
        knowledge_port,
        settings,
        assistants_store=_shared_assistants_store,
        tenant_settings_store=_shared_tenant_settings_store,
        tools_store=_shared_tools_store,
        scenarios_store=_shared_scenarios_store,
        assignment_store=_shared_assignment_store,
        timing_store=_shared_timing_store,
    )

    # Merge CRM-authored live-FAQ into the KB that /assist + Copilot ground on,
    # so an authored entry surfaces in their answers immediately.
    from chatbot.features.chat.adapters.merged_knowledge import MergedKnowledgeAdapter

    _assist_genai = _build_genai_client(settings)
    _assist_live_store = build_live_faq_store(settings, _assist_genai)  # type: ignore[arg-type]
    _assist_embedder = (
        VertexEmbedder(_assist_genai, settings.embedding_model)
        if _assist_genai is not None
        else None
    )
    assist_knowledge_port = MergedKnowledgeAdapter(
        knowledge_port, _assist_live_store, _assist_embedder
    )

    # --- pgvector knowledge base (subsystems A+B; default-off) ---
    kb_pg_adapter = None
    if settings.knowledge_pg_enabled and settings.knowledge_database_url:
        from chatbot.features.chat.adapters.pgvector_knowledge import PgVectorKnowledgeAdapter
        from chatbot.features.chat.kb_db import build_engine, build_session_maker
        from chatbot.features.chat.kb_knowledge_router import build_kb_knowledge_router
        from chatbot.features.chat.kb_repository import PgKbRepository

        kb_engine = build_engine(settings.knowledge_database_url)
        kb_session_maker = build_session_maker(kb_engine)
        kb_repo = PgKbRepository(kb_session_maker)
        kb_embedder = (
            VertexEmbedder(_assist_genai, settings.embedding_model)
            if _assist_genai is not None
            else None
        )
        if kb_embedder is not None:
            kb_pg_adapter = PgVectorKnowledgeAdapter(kb_repo, kb_embedder, settings.kb_score_floor)
            app.include_router(build_kb_knowledge_router(kb_repo, kb_embedder, settings))
            app.state.kb_engine = kb_engine  # for init in lifespan startup
        else:
            # Enabled but embeddings unavailable → skip mounting so uploads 404
            # rather than every doc silently failing to embed. Log for visibility.
            import structlog as _sl

            _sl.get_logger(__name__).warning(
                "knowledge_pg_enabled but no embedder (genai unavailable); /kb/knowledge not mounted"
            )

    if kb_pg_adapter is not None:
        assist_knowledge_port = MergedKnowledgeAdapter(
            knowledge_port, _assist_live_store, _assist_embedder, pg_port=kb_pg_adapter
        )

    @app.on_event("startup")
    async def _init_kb_db() -> None:
        engine = getattr(app.state, "kb_engine", None)
        if engine is not None:
            from chatbot.features.chat.kb_db import init_kb_db

            await init_kb_db(engine)

    # --- RSA (roadside assistance) incident log (default-off) ---
    # Initialized to None so the RBAC block below (which reuses this instance
    # for the Customer 360 router) can check availability without a
    # NameError when RSA is disabled.
    rsa_repo = None
    if settings.rsa_enabled and settings.rsa_database_url:
        from chatbot.features.rsa.rsa_db import build_engine as build_rsa_engine
        from chatbot.features.rsa.rsa_db import build_session_maker as build_rsa_session_maker
        from chatbot.features.rsa.rsa_repository import PgRsaRepository
        from chatbot.features.rsa.rsa_router import build_rsa_router

        rsa_engine = build_rsa_engine(settings.rsa_database_url)
        rsa_session_maker = build_rsa_session_maker(rsa_engine)
        rsa_repo = PgRsaRepository(rsa_session_maker)
        app.include_router(build_rsa_router(rsa_repo, settings))
        app.state.rsa_engine = rsa_engine

    @app.on_event("startup")
    async def _init_rsa_db() -> None:
        engine = getattr(app.state, "rsa_engine", None)
        if engine is not None:
            from chatbot.features.rsa.rsa_db import init_rsa_db

            await init_rsa_db(engine)

    # --- RBAC (roles/permissions; default-off) ---
    # authz_validator is initialized out here (like authz_repo) so the P6 block
    # below can hand both to the routing/workforce routers without a NameError
    # when RBAC is off. `require_permission` already treats a None pair as
    # "fall back to the shared-secret x-api-key check", which is exactly the
    # right behaviour for an RBAC-disabled tenant.
    authz_repo = None
    authz_validator = None
    sla_policy_repo = None
    if settings.rbac_enabled and settings.rbac_database_url:
        from chatbot.features.authz.chatwoot_role_mirror import ChatwootRoleMirror
        from chatbot.features.authz.db import build_engine as build_authz_engine
        from chatbot.features.authz.db import build_session_maker as build_authz_session_maker
        from chatbot.features.authz.identity import TokenValidator
        from chatbot.features.authz.repository import AuthzRepository
        from chatbot.features.authz.router import build_authz_router
        from chatbot.features.chat.sla_policy_db import build_engine as build_sla_policy_engine
        from chatbot.features.chat.sla_policy_db import (
            build_session_maker as build_sla_policy_session_maker,
        )
        from chatbot.features.chat.sla_policy_repository import SlaPolicyRepository
        from chatbot.features.chat.sla_policy_router import build_sla_policy_router

        authz_engine = build_authz_engine(settings.rbac_database_url)
        authz_session_maker = build_authz_session_maker(authz_engine)
        authz_repo = AuthzRepository(authz_session_maker)
        authz_validator = TokenValidator(settings)
        authz_mirror = ChatwootRoleMirror(settings)
        app.include_router(
            build_authz_router(authz_repo, authz_validator, settings, mirror=authz_mirror)
        )
        app.state.authz_engine = authz_engine
        app.state.authz_repo = authz_repo

        from chatbot.features.chat.audit_router import build_audit_router

        app.include_router(build_audit_router(audit_log, authz_repo, authz_validator, settings))

        sla_policy_engine = build_sla_policy_engine(settings.rbac_database_url)
        sla_policy_repo = SlaPolicyRepository(build_sla_policy_session_maker(sla_policy_engine))
        app.include_router(
            build_sla_policy_router(sla_policy_repo, authz_repo, authz_validator, settings)
        )
        app.state.sla_policy_engine = sla_policy_engine

        # Reuses the pic_store/dealer_store instances constructed unconditionally
        # above — RBAC gates only the admin CRUD router, not the stores themselves.
        from chatbot.features.chat.pic_admin_router import build_pic_admin_router

        app.include_router(
            build_pic_admin_router(pic_store, dealer_store, authz_repo, authz_validator, settings)
        )

        # P3: the case-record panel's read/write endpoints. The merge-safe
        # attribute writer, not the raw request method: the custom-attributes
        # endpoint REPLACES the whole object, so a bare POST would wipe
        # case_category, recording_url and everything else on the conversation.
        if chatwoot_client is not None:
            from chatbot.features.chat.case_fields_router import build_case_fields_router

            app.include_router(
                build_case_fields_router(
                    lambda conv_id: chatwoot_client._request(
                        "GET", f"/conversations/{conv_id}", None
                    ),
                    chatwoot_client._merge_custom_attributes,
                    authz_repo,
                    authz_validator,
                    settings,
                    dealer_store=dealer_store,
                )
            )

        # Package F: DMS/TSP integration shell admin CRUD + connection test.
        # Reuses the dms_config_store instance constructed unconditionally
        # above, same pattern as pic_store/dealer_store. The error handler
        # must be installed on the app itself (FastAPI validation-error
        # handlers can't be scoped to one router) so a malformed `credential`
        # in a PUT body never echoes back in a 422's `input` field -- see
        # dms_admin_router.py's module docstring.
        from chatbot.features.chat.dms_admin_router import (
            build_dms_admin_router,
            install_credential_safe_error_handler,
        )

        app.include_router(
            build_dms_admin_router(dms_config_store, authz_repo, authz_validator, settings)
        )
        install_credential_safe_error_handler(app)

        # Customer 360 foundational lookup (Track 5). Reuses the already-built
        # chatwoot_client / rsa_repo instances above rather than constructing
        # second copies. Both are optional (chatwoot_client is None on the
        # zendesk crm_provider path; rsa_repo is None when RSA logging is
        # disabled), so only mount when both prerequisites are actually wired.
        if chatwoot_client is not None and rsa_repo is not None:
            from chatbot.features.chat.customer360_router import build_customer360_router

            app.include_router(
                build_customer360_router(
                    chatwoot_client,
                    rsa_repo,
                    authz_repo,
                    authz_validator,
                    settings,
                    dms_config_store=dms_config_store,
                    dms_client=dms_client,
                )
            )
        else:
            import structlog as _sl

            _sl.get_logger(__name__).warning(
                "customer360_prerequisites_missing",
                detail=(
                    "RBAC is enabled but chatwoot_client and/or rsa_repo is not "
                    "available; /admin/customer360 not mounted"
                ),
                chatwoot_available=chatwoot_client is not None,
                rsa_available=rsa_repo is not None,
            )
    elif settings.rbac_enabled:
        import structlog as _sl

        _sl.get_logger(__name__).warning(
            "rbac_enabled_but_no_database_url",
            detail="RBAC_ENABLED is true but RBAC_DATABASE_URL is empty; /authz not mounted",
        )

    # Task 9: the agent softphone's token-mint + registration endpoints.
    # Mounted here, right after the RBAC block, so `authz_repo`/`authz_
    # validator` reflect whatever that block actually built above -- the real
    # pair when RBAC is on, still None otherwise.
    #
    # Whole-branch review fix (Important 7): the paragraph this replaced
    # claimed a None pair falls back to the shared-secret x-api-key check.
    # It does not, and the difference is deliberate:
    # `require_permission_with_identity` (features/authz/deps.py) EXPLICITLY
    # refuses the shared-secret path and 401s whenever repo/validator are
    # None or rbac_enabled is off -- "a shared secret identifies a service,
    # not a person, and the only caller of this dependency mints a
    # credential in a specific person's name" (its own docstring). So the
    # real prerequisite for this feature to work at all is
    # RBAC_ENABLED=true and a mounted /authz (i.e. RBAC_DATABASE_URL set);
    # with RBAC off, every /voice/agent/* call 401s regardless of
    # PHONE_AGENT_SOFTPHONE_ENABLED. Documented in example.env next to that
    # flag. This router is still mounted unconditionally (like the routing
    # config router above) because its own routes 404 internally when
    # `phone_agent_softphone_enabled` is off, independent of RBAC.
    from chatbot.features.chat.phone.softphone_router import build_softphone_router

    app.include_router(
        build_softphone_router(
            settings,
            softphone_registry,
            repo=authz_repo,
            validator=authz_validator,
        )
    )

    @app.on_event("startup")
    async def _init_authz_db() -> None:
        engine = getattr(app.state, "authz_engine", None)
        repo = getattr(app.state, "authz_repo", None)
        if engine is not None and repo is not None:
            from chatbot.features.authz.db import init_authz_db
            from chatbot.features.authz.seed import seed_defaults

            await init_authz_db(engine)
            await seed_defaults(repo)
            if settings.rbac_bootstrap_admin_user_id is not None:
                # Break-glass bootstrap: idempotent (assign_role no-ops if the
                # assignment already exists), safe to run on every startup.
                await repo.assign_role(settings.rbac_bootstrap_admin_user_id, "administrator")

        sla_policy_engine = getattr(app.state, "sla_policy_engine", None)
        if sla_policy_engine is not None:
            from chatbot.features.chat.sla_policy_db import init_sla_policy_db

            await init_sla_policy_db(sla_policy_engine)

    # --- P6: agent presence, custom statuses & the workforce dashboard ---
    # Sits after the RBAC block, not with the Phase 5 constructions above, for
    # one reason: both routers here gate on a permission, and with RBAC on
    # `require_permission` needs the repo/validator that block builds. With RBAC
    # off both are None and both routers fall back to the shared-secret check,
    # exactly as they did before this package.
    #
    # The routing config router is mounted unconditionally, as it always was --
    # `routing_enabled` gates automatic agent *selection* inside the handler,
    # never the endpoints' existence. `audit`/`authz_repo`/`validator` are new
    # (P6 task 8's supervisor reassignment): all three reuse the instances built
    # once above rather than constructing duplicates, same convention as
    # pic_store/dealer_store.
    app.include_router(
        build_routing_router(
            settings,
            _routing_priority_store,
            _routing_presence,
            _routing_svc,
            _routing_assigner,
            audit=audit_log,
            authz_repo=authz_repo,
            validator=authz_validator,
        )
    )

    # The dashboard reads the presence-event log directly, so it is gated on the
    # flag that fills that log. Mounting it with presence tracking off would
    # serve a page of rows whose every presence field is null -- technically
    # honest, but indistinguishable from a broken dashboard.
    if settings.presence_tracking_enabled:
        app.include_router(
            build_workforce_router(
                settings,
                authz_repo,
                authz_validator,
                presence_fetcher=_routing_presence,
                presence_store=_presence_event_store,
                status_store=_custom_status_store,
            )
        )

    # The status-selection + catalogue router (review-final C1's other half).
    # Without this mount `set_status` still has no HTTP caller, all four
    # endpoints 404, and requirements 4.12/4.13/4.14/4.17 stay dark end to end
    # no matter how green their unit tests are -- the poller would remain the
    # only writer of presence events and would only ever write Chatwoot's three
    # native values. It sits here, after the RBAC block, for the same reason
    # the workforce router does: `require_permission` needs that block's
    # repo/validator when RBAC is on, and both are None (shared-secret
    # fallback) when it is off.
    #
    # It is handed the SAME `_custom_status_store`/`_presence_event_store`
    # built at the top of this function rather than letting the factory
    # construct its own -- same convention as the workforce router and
    # pic_store/dealer_store above. Instance identity is not what makes this
    # correct (Firestore is the shared state), but a second pair here would be
    # two more objects to keep configured in step for no gain.
    #
    # Gated on `presence_custom_statuses_enabled` rather than mounted
    # unconditionally, which is the `/webhooks/phone/dial-status` precedent in
    # features/chat/router.py: a tenant that never enabled custom statuses gets
    # FastAPI's own 404 with no handler code reachable at all, instead of an
    # endpoint that answers 200 `{"disabled": true}` -- a shape a UI could
    # mistake for a status change that worked. The router *also* self-gates on
    # the same flag (see its module docstring); that guard is for direct
    # in-process callers and is not made redundant by this one.
    if settings.presence_custom_statuses_enabled:
        app.include_router(
            build_status_router(
                settings,
                authz_repo,
                authz_validator,
                status_store=_custom_status_store,
                presence_store=_presence_event_store,
            )
        )

    # Seeding the catalogue (the eight §4.17 names plus `acw` and `offline`,
    # ten documents) is create-only -- an operator who re-tinted "Lunch" keeps
    # their edit across restarts, the same discipline as
    # TargetsStore.seed_from_settings -- so it is safe to run on every boot.
    # It is still gated on the custom-status flag, because seeding is a real
    # Firestore write: with the flag off nothing SELECTS from the catalogue, and
    # writing ten documents into every tenant's Firestore anyway would break
    # the "all flags off changes nothing" guarantee this package is sold on.
    # Note what the gate does NOT withhold: `CustomStatusStore.get` falls back
    # to the shipped definitions when a document is absent, so ACW and the
    # threshold sweeper resolve their statuses on an unseeded tenant too (the
    # I2 trap). This flag gates selecting and editing, not resolving.
    if settings.presence_custom_statuses_enabled:

        @app.on_event("startup")
        async def _seed_custom_statuses() -> None:
            created = await _custom_status_store.seed()
            import structlog as _sl

            _sl.get_logger(__name__).info("custom_statuses_seeded", created=created)

    # Four schedulers, each returning None when its own flag is off (no
    # BackgroundScheduler is even constructed then), each with the shutdown hook
    # convention the metrics/SLA schedulers already use in this file.
    presence_poller = start_presence_poller(settings)
    if presence_poller is not None:

        @app.on_event("shutdown")
        def _stop_presence_poller() -> None:
            presence_poller.shutdown(wait=False)

    presence_threshold_sweeper = start_presence_threshold_sweeper(settings)
    if presence_threshold_sweeper is not None:

        @app.on_event("shutdown")
        def _stop_presence_threshold_sweeper() -> None:
            presence_threshold_sweeper.shutdown(wait=False)

    # ACW's timeout is derived from the stored event timestamp and self-heals on
    # the next read, so this sweeper is not what makes the timeout correct -- it
    # is what bounds how long an agent who forgot to leave ACW waits to be
    # noticed on a quiet queue.
    acw_sweeper = start_acw_sweeper(settings, _acw_controller)
    if acw_sweeper is not None:

        @app.on_event("shutdown")
        def _stop_acw_sweeper() -> None:
            acw_sweeper.shutdown(wait=False)

    # Reuses the same routing service and assigner /routing/assign uses, so the
    # sweeper can never disagree with the event-driven path about who is
    # eligible. Gated on routing_enabled AND routing_sweep_enabled.
    routing_sweeper = start_routing_sweeper(settings, _routing_svc, _routing_assigner)
    if routing_sweeper is not None:

        @app.on_event("shutdown")
        def _stop_routing_sweeper() -> None:
            routing_sweeper.shutdown(wait=False)

    # --- P9: per-agent alert preferences (/alerts/rules) ---------------------
    # Sits here, after the RBAC block, for the same reason the workforce and
    # status routers do: both of its permissions go through `require_permission`,
    # which needs that block's repo/validator when RBAC is on and falls back to
    # the shared-secret x-api-key check when it is off. It is handed the SAME
    # `authz_repo`/`authz_validator` instances built once above rather than
    # constructing a second pair -- same convention as pic_store/dealer_store.
    #
    # **Mounted unconditionally, unlike the custom-status router**, and the
    # difference is deliberate. `build_status_router` is gated on its flag so an
    # unenabled tenant gets FastAPI's own 404 with no handler reachable. Here the
    # consumer requires the opposite: the fork's preferences page
    # (`ProtonAlertPreferencesPage.vue`, patch 0057) renders
    # `rules_router.py`'s `{"disabled": true, "reason": ...}` body VERBATIM, and
    # 404ing instead would leave the page unable to tell "this tenant has not
    # enabled alert rules" apart from "the backend is the wrong version" -- it
    # would guess, and the whole point of that body is that it does not have to.
    # `alert_rules_enabled` is enforced inside every endpoint, so mounting costs
    # nothing on an unenabled tenant: no Firestore client is constructed
    # (`AlertRuleStore` builds one lazily per call) and no store read happens.
    # The same precedent `/metrics/ai-cost` and `/assist/translate` set.
    #
    # Until this mount existed the five endpoints 404ed against a live backend
    # and every agent silently got `BUILT_IN_DEFAULTS` -- the designed fallback,
    # but it made the whole per-agent override layer unreachable by the people it
    # exists for. `test_p9_wiring.py` drives the real app for exactly that.
    from chatbot.features.alerts.rules_router import build_rules_router
    from chatbot.features.chat.phone.recording_router import build_recording_router
    from chatbot.features.taxonomy.router import build_taxonomy_admin_router

    app.include_router(build_rules_router(settings, authz_repo, authz_validator))
    app.include_router(build_taxonomy_admin_router(settings))

    @app.on_event("startup")
    async def _seed_taxonomy_store() -> None:
        """Seed the taxonomy store from the three CASE_*_JSON settings.

        Dispatched, never awaited. A first boot against an empty store is 346
        sequential Firestore writes -- 15-30s -- and awaiting that here holds the
        container below its health check. A populated store costs one read, so
        the steady state is nearly free either way.

        `example.env` already documents these vars as "the seed only" once a
        tenant's store is populated. Until this hook existed no tenant's store
        ever was, and the taxonomy admin page rendered empty on a tenant whose
        config held the full Appendix A taxonomy.
        """
        if not settings.taxonomy_admin_enabled:
            return

        import asyncio

        from chatbot.features.taxonomy.seed import seed_taxonomy_from_env
        from chatbot.features.taxonomy.store import build_taxonomy_store

        async def _run() -> None:
            try:
                created = await seed_taxonomy_from_env(build_taxonomy_store(settings), settings)
                _log.info("taxonomy_startup_seed_complete", newly_created=created)
            except Exception as exc:
                # Broad on purpose: a non-string entry in one of the CASE_*_JSON
                # env vars raises AttributeError from inside the seeder, and this
                # is what keeps that from killing the boot. Narrowing this to
                # e.g. ValueError would let such an error escape the background
                # task, which only produces an unretrieved-exception log.
                # error() + exc_info so a malformed CASE_*_JSON var is diagnosable
                # -- the only operator-visible symptom otherwise is an empty
                # admin page, with nothing pointing at which var or why.
                _log.error("taxonomy_startup_seed_failed", error=str(exc), exc_info=True)

        # Held on app.state so the task is not garbage-collected mid-flight.
        app.state.taxonomy_seed_task = asyncio.create_task(_run())

    # P11 task 1: `GET /calls/{conversation_id}/recording`. Mounted here for the
    # same reason the two above are: the router was written, unit-tested against
    # its own throwaway `FastAPI()` and never mounted, so on a live backend the
    # endpoint 404ed and `call_recording_retrieval_enabled` had no consumer that
    # any deployment could reach. Mounting is free on an unenabled tenant --
    # every route is `require_permission("call_recording.listen")`-gated and
    # `call_recording_retrieval_enabled` is re-checked inside the handler, so an
    # unauthenticated caller gets 401 and a permitted caller on a tenant with the
    # flag off gets 404. `test_p11_wiring.py` drives the real app for exactly
    # that, the way `test_p10_wiring.py`/`test_p6_wiring.py` do.
    #
    # What this does NOT yet do: the handler reads an in-process registry that
    # nothing in production writes to (see `recording_router.py`'s docstring), so
    # against a real conversation it answers the "no recording exists" state. The
    # mount makes the endpoint and its permission gate real; the Chatwoot
    # custom-attribute read that would populate it is still owed.
    app.include_router(build_recording_router(settings))

    # --- Proton AI-assist (rewired Captain AI) ---
    _assist_router = _wire_assist(
        app,
        assist_knowledge_port,
        settings,
        assistants_store=_shared_assistants_store,
        tenant_settings_store=_shared_tenant_settings_store,
    )

    # P7 task 9/11: bind the resolved-case summariser to the route just built.
    # This is the whole reason `_wire_assist` returns its router -- see the
    # summariser's construction above, and `resolved_case_adapters.py` for why
    # the automatic path executes this endpoint rather than a copy of its prompt.
    # A missing route logs once at boot instead of once per resolve, because
    # "the summariser was never wired" is a deployment fact, not an event.
    if not _resolved_case_summarizer.bind(find_summarize_endpoint(_assist_router)):
        _log.warning(
            "resolved_case_summarizer_not_bound",
            detail=(
                "POST /assist/summarize was not found on the assist router, so "
                "auto-summary on resolve and the resolved-case index will both "
                "no-op. Resolving a conversation is unaffected."
            ),
        )

    # P7 task 3: POST /assist/translate, the agent-facing translate action.
    # Mounted here rather than with the other RBAC-gated routers because it is an
    # /assist endpoint and belongs beside them, and because by this point
    # `authz_repo`/`authz_validator` exist: it gates on the `translation.use`
    # permission, and `require_permission` needs that pair when RBAC is on. Both
    # are the SAME instances the RBAC block built (never a second copy, same
    # convention as pic_store/dealer_store), and both are None with RBAC off,
    # which `require_permission` already treats as today's shared-secret check.
    #
    # It reuses `_assist_genai` rather than building a fifth client: the same
    # Gemini client the merged-knowledge assist path already holds, and None (SDK
    # or credentials unavailable) surfaces as this endpoint's own 502 with no note
    # posted, which is its documented model-failure path.
    #
    # Mounted UNCONDITIONALLY, unlike P6's status router which is gated on its
    # flag. The difference is what a disabled call returns: the status router
    # would have answered 200 `{"disabled": true}` to a status *write*, a shape a
    # UI could read as a change that worked. A disabled translate returns
    # `{"disabled": true, "reason": ...}` with no `translation` field at all --
    # there is nothing there to mistake for a successful translation -- and the
    # fork's Translate button (patch 0055) then reports a legible refusal instead
    # of a 404 that reads as a broken deployment. No Gemini call and no note
    # happen on that path; see translate_router.py's module docstring.
    app.include_router(
        build_translate_router(
            settings,
            # Re-labelled at hand-over: `_assist_genai` is shared with the
            # merged-knowledge embedders, and `embed_content` self-labels
            # `embed` regardless, so the two consumers of this one client keep
            # their spend apart in the cost report.
            with_surface(_assist_genai, SURFACE_ASSIST_TRANSLATE),
            ticketing_port,
            authz_repo,
            authz_validator,
        )
    )

    # --- Ask Copilot (multi-turn) ---
    _wire_copilot(
        app,
        assist_knowledge_port,
        settings,
        assistants_store=_shared_assistants_store,
        tenant_settings_store=_shared_tenant_settings_store,
        tools_store=_shared_tools_store,
        scenarios_store=_shared_scenarios_store,
        assignment_store=_shared_assignment_store,
    )

    _wire_metrics_features(app, settings)

    # --- SLA-timer escalation engine (Chatwoot has no native SLA engine) ---
    # Guarded behind sla_engine_enabled (default OFF) exactly like the metrics
    # scheduler, so nothing scans unless a deployment explicitly opts in.
    # Task 15: reuse the SAME PicRegistry/SmtpEmailSender already constructed
    # for EscalationNotifier (not new instances) so PIC routing config is
    # read from one place, and post the note via the same ChatwootAdapter
    # method (add_private_note) the escalation path uses.
    sla_scheduler = start_sla_scheduler(
        settings,
        audit_log,
        twilio_adapter=twilio_adapter,
        policy_repo=sla_policy_repo,
        pic_registry=pic_registry,
        email_sender=email_sender,
        note_poster=chatwoot_client.add_private_note if chatwoot_client is not None else None,
    )
    if sla_scheduler is not None:

        @app.on_event("shutdown")
        def _stop_sla_scheduler() -> None:
            sla_scheduler.shutdown(wait=False)

    # --- Task Timers & Agent Reminders (Phase 6) ---
    app.include_router(build_tasks_router(settings))

    # --- P13: the deep health check, and the two retention schedules ---------
    # All three modules below shipped complete, unit-tested and WITHOUT A CALLER
    # (see docs/analysis/2026-08-09-blocked-work-register.md §3c-4). Their unit
    # tests passed because they called the inner function directly, one layer
    # below the bug, which is this run's recurring failure. `test_p13_wiring.py`
    # drives the real app for each.

    # `GET /healthz` -- probes what is configured, answers 503 when a dependency
    # actually failed, and is bounded at 2s (concurrent probes, so the bound does
    # not multiply). Mounted unconditionally: gating a health check behind a flag
    # would mean the tenants that most need one are the ones without it.
    #
    # `GET /` is deliberately UNTOUCHED above. It is the container's liveness
    # probe in docker-compose.tenant.yml, and a liveness probe that fails on a
    # dependency outage restarts a healthy process in a loop -- turning one
    # broken dependency into an outage. Liveness and readiness are different
    # questions and now have different endpoints; point monitoring uptime checks
    # at /healthz (docs/runbooks/monitoring-alerts.md §5 item 1).
    #
    # The engines are read per request from `app.state`, so the probe set follows
    # the flags that actually built something: a `None` engine means that feature
    # is off for this tenant and contributes no subsystem, because an absent
    # dependency is not an unhealthy one.
    app.include_router(
        build_health_router(
            settings,
            lambda: {
                "rbac_database": getattr(app.state, "authz_engine", None),
                "knowledge_database": getattr(app.state, "kb_engine", None),
                "rsa_database": getattr(app.state, "rsa_engine", None),
            },
        )
    )

    # Audit-log retention. Off by default; when on, a daily tick reports how many
    # rows are past AUDIT_LOG_RETENTION_DAYS and deletes NOTHING -- the audit-log
    # port has no delete method and `AuditEntry` carries no document id, so there
    # is nothing to address a deletion to. The row source is wired anyway (it is
    # the same port `/cases/{id}/audit` reads), because a daily honest count of
    # trail past retention is useful and because it proves the whole path but the
    # last step. Deliberately no `delete_func=`: see audit_purge.py's docstring.
    audit_purge_scheduler = start_audit_purge_job(
        settings, source=build_audit_row_source(audit_log)
    )
    app.state.audit_purge_scheduler = audit_purge_scheduler
    if audit_purge_scheduler is not None:

        @app.on_event("shutdown")
        def _stop_audit_purge_scheduler() -> None:
            audit_purge_scheduler.shutdown(wait=False)

    # Call-recording retention. Off by default; when on, the tick is scheduled
    # but neither a candidate source nor a deleter is passed, so it reports
    # `not_executable` and touches nothing. That is deliberate and it is the
    # honest state: there is no Twilio recording-delete adapter and no store that
    # lists recordings due for purge, so **the 90-day recording policy is not in
    # force on any tenant**. Deleting a customer's call recording is
    # irreversible, so the scheduling lands first and the destructive step waits
    # for explicit configuration rather than being inferred from a flag.
    recording_retention_scheduler = start_recording_retention_job(settings)
    app.state.recording_retention_scheduler = recording_retention_scheduler
    if recording_retention_scheduler is not None:

        @app.on_event("shutdown")
        def _stop_recording_retention_scheduler() -> None:
            recording_retention_scheduler.shutdown(wait=False)

    @app.get("/")
    def health_check() -> dict[str, str]:
        return {
            "status": "healthy",
            "crm_provider": settings.crm_provider,
            "voice_provider": settings.voice_provider,
            "model": settings.gemini_model,
        }

    return app


app = bootstrap_application()
