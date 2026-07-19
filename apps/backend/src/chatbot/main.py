from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from chatbot.features.assist.copilot_router import build_copilot_router
from chatbot.features.assist.router import build_assist_router
from chatbot.features.chat.adapters.assistants_store import build_assistants_store
from chatbot.features.chat.adapters.audit_log import build_audit_log
from chatbot.features.chat.adapters.bigquery_metrics import build_metrics_port
from chatbot.features.chat.adapters.chatwoot import ChatwootAdapter
from chatbot.features.chat.adapters.gcp_voice import GeminiTextToSpeechAdapter
from chatbot.features.chat.adapters.handoff_store import build_handoff_store
from chatbot.features.chat.adapters.inbox_assignment_store import build_inbox_assignment_store
from chatbot.features.chat.adapters.live_faq import VertexEmbedder, build_live_faq_store
from chatbot.features.chat.adapters.mock import InMemoryKnowledgeAdapter, MockVoiceAdapter
from chatbot.features.chat.adapters.noop_conversation_log import NoOpConversationLog
from chatbot.features.chat.adapters.scenarios_store import build_scenarios_store
from chatbot.features.chat.adapters.sunshine_conversations import SunshineConversationsAdapter
from chatbot.features.chat.adapters.tenant_settings_store import build_tenant_settings_store
from chatbot.features.chat.adapters.tools_store import build_tools_store
from chatbot.features.chat.adapters.twilio_channel import TwilioChannelAdapter
from chatbot.features.chat.adapters.vertex_search import VertexAISearchAdapter
from chatbot.features.chat.adapters.zammad import ZammadClient
from chatbot.features.chat.adapters.zendesk import ZendeskAdapter
from chatbot.features.chat.escalation_notifier import EscalationNotifier
from chatbot.features.chat.faq_admin_router import build_faq_admin_router
from chatbot.features.chat.handoff_bridge import HandoffBridge
from chatbot.features.chat.kb_assistants_router import build_kb_assistants_router
from chatbot.features.chat.kb_documents_router import build_kb_documents_router
from chatbot.features.chat.kb_inboxes_router import build_kb_inboxes_router
from chatbot.features.chat.kb_scenarios_router import build_kb_scenarios_router
from chatbot.features.chat.kb_settings_router import build_kb_settings_router
from chatbot.features.chat.kb_suggest_router import build_kb_suggest_router
from chatbot.features.chat.kb_tools_router import build_kb_tools_router
from chatbot.features.chat.pic_registry import build_pic_registry
from chatbot.features.chat.ports import (
    ChatPort,
    ConversationLogPort,
    HumanAgentBridgePort,
    KnowledgePort,
    TextToSpeechPort,
    TicketingPort,
)
from chatbot.features.chat.router import build_chat_router
from chatbot.features.chat.service import OrchestratorService
from chatbot.features.chat.sla import start_sla_scheduler
from chatbot.features.metrics.anomaly_router import build_metrics_anomaly_router
from chatbot.features.metrics.dashboard_router import build_metrics_query_router
from chatbot.features.metrics.email_port import build_email_report_port
from chatbot.features.metrics.email_sender import SmtpEmailSender
from chatbot.features.metrics.export_router import build_metrics_export_router
from chatbot.features.metrics.faq_feedback_adapter import build_faq_feedback_port
from chatbot.features.metrics.faq_router import build_faq_router
from chatbot.features.metrics.qa_adapter import build_qa_label_port
from chatbot.features.metrics.qa_router import build_qa_router
from chatbot.features.metrics.query_adapter import build_metrics_query_port
from chatbot.features.metrics.scheduler import start_metrics_scheduler, start_report_scheduler
from chatbot.platform.config import Settings, get_settings
from chatbot.platform.logger import configure_logging
from chatbot.platform.server import create_app


def _build_genai_client(settings: Settings) -> object | None:
    """Build a google-genai client for embeddings (ADC / Vertex), mirroring the
    orchestrator. Returns None if the SDK/credentials are unavailable so live-FAQ
    wiring falls back to Vertex-Search-only suggestions and boot never breaks."""
    try:
        from google.genai import Client  # noqa: PLC0415 — lazy: boot without the SDK

        if settings.google_genai_use_vertexai:
            return Client(
                vertexai=True,
                project=settings.vertex_project_id,
                location=settings.vertex_location,
            )
        return Client()
    except Exception:
        return None


def _wire_assist(
    app: FastAPI,
    knowledge_port: KnowledgePort,
    settings: Settings,
    assistants_store: object | None = None,
    tenant_settings_store: object | None = None,
) -> None:
    """Wire the three /assist/* endpoints and add Chatwoot origins to CORS."""
    genai_client = _build_genai_client(settings)
    app.include_router(
        build_assist_router(
            settings,
            knowledge_port,
            genai_client,
            assistants_store=assistants_store,  # type: ignore[arg-type]
            tenant_settings_store=tenant_settings_store,  # type: ignore[arg-type]
        )
    )

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
            allow_headers=["x-api-key", "content-type"],
        )


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
            genai_client,
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
        )
    )

    faq_port = build_faq_feedback_port(settings)
    app.include_router(build_faq_router(faq_port, settings))


def _wire_metrics_features(app: FastAPI, settings: Settings) -> None:
    """Wire QA-labelling router, dashboard read API, and metrics-scheduler shutdown."""
    qa_port = build_qa_label_port(settings)
    app.include_router(build_qa_router(qa_port, settings))

    query_port = build_metrics_query_port(settings)
    app.include_router(build_metrics_query_router(query_port))
    app.include_router(build_metrics_export_router(query_port))
    app.include_router(build_metrics_anomaly_router(query_port, settings))

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
    pic_registry = build_pic_registry(settings)
    email_sender = SmtpEmailSender(settings)

    if settings.crm_provider == "zendesk":
        zendesk_client = ZendeskAdapter(settings)
        chat_port = zendesk_client
        ticketing_port = zendesk_client
        if settings.zendesk_app_id and settings.zendesk_key_id and settings.zendesk_secret_key:
            human_agent_bridge = SunshineConversationsAdapter(settings)
            handoff_bridge = HandoffBridge(store=build_handoff_store(settings))
    else:
        # Own Zammad ticketing directly (when enabled) so a complaint escalation
        # POSTs the back-office ticket ourselves instead of relying on the
        # `escalate` label + external agent-service sync.
        zammad_client = ZammadClient(settings) if settings.zammad_enabled else None
        # Construct without escalation_notifier first so we can pass _request into it.
        chatwoot_client = ChatwootAdapter(
            settings, zammad=zammad_client, pic_registry=pic_registry
        )
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

    # --- Phase-2: wire EscalationNotifier into ChatwootAdapter now that twilio is ready ---
    # Post-construction injection is safe: escalation only fires inside async request
    # handlers, which run after bootstrap_application() returns.
    if chatwoot_client is not None:
        escalation_notifier = EscalationNotifier(
            settings=settings,
            pic_registry=pic_registry,
            email_sender=email_sender,
            twilio_adapter=twilio_adapter,
            chatwoot_request=chatwoot_client._request,  # type: ignore[arg-type]
        )
        chatwoot_client._escalation_notifier = escalation_notifier  # type: ignore[assignment]

    # --- Metrics port (per-turn BigQuery streaming) ---
    metrics_port = build_metrics_port(settings)

    # --- Audit log (case state transition trail) ---
    audit_log = build_audit_log(settings)

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
    )

    app.include_router(
        build_chat_router(
            orchestrator=orchestrator,
            handoff_bridge=handoff_bridge,
            human_agent_bridge=human_agent_bridge,
            twilio_adapter=twilio_adapter,
            audit_log=audit_log,
        )
    )

    # --- Shared stores (built once, passed to both agent-assist and copilot) ---
    _shared_assistants_store = build_assistants_store(settings)
    _shared_tenant_settings_store = build_tenant_settings_store(settings)
    _shared_tools_store = build_tools_store(settings)
    _shared_scenarios_store = build_scenarios_store(settings)
    _shared_assignment_store = build_inbox_assignment_store(settings)

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
    )

    # Merge CRM-authored live-FAQ into the KB that /assist + Copilot ground on,
    # so an authored entry surfaces in their answers immediately.
    from chatbot.features.chat.adapters.merged_knowledge import MergedKnowledgeAdapter
    _assist_genai = _build_genai_client(settings)
    _assist_live_store = build_live_faq_store(settings, _assist_genai)  # type: ignore[arg-type]
    _assist_embedder = (
        VertexEmbedder(_assist_genai, settings.embedding_model)
        if _assist_genai is not None else None
    )
    assist_knowledge_port = MergedKnowledgeAdapter(
        knowledge_port, _assist_live_store, _assist_embedder
    )

    # --- Proton AI-assist (rewired Captain AI) ---
    _wire_assist(
        app,
        assist_knowledge_port,
        settings,
        assistants_store=_shared_assistants_store,
        tenant_settings_store=_shared_tenant_settings_store,
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
    sla_scheduler = start_sla_scheduler(settings, audit_log, twilio_adapter=twilio_adapter)
    if sla_scheduler is not None:

        @app.on_event("shutdown")
        def _stop_sla_scheduler() -> None:
            sla_scheduler.shutdown(wait=False)

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
