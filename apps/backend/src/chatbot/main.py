from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from chatbot.features.chat.adapters.bigquery_metrics import build_metrics_port
from chatbot.features.chat.adapters.chatwoot_zammad import ChatwootZammadAdapter
from chatbot.features.chat.adapters.gcp_voice import GeminiTextToSpeechAdapter
from chatbot.features.chat.adapters.handoff_store import build_handoff_store
from chatbot.features.chat.adapters.mock import InMemoryKnowledgeAdapter, MockVoiceAdapter
from chatbot.features.chat.adapters.noop_conversation_log import NoOpConversationLog
from chatbot.features.chat.adapters.sunshine_conversations import SunshineConversationsAdapter
from chatbot.features.chat.adapters.twilio_channel import TwilioChannelAdapter
from chatbot.features.chat.adapters.vertex_search import VertexAISearchAdapter
from chatbot.features.chat.adapters.zendesk import ZendeskAdapter
from chatbot.features.chat.handoff_bridge import HandoffBridge
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
from chatbot.features.metrics.qa_adapter import build_qa_label_port
from chatbot.features.metrics.qa_router import build_qa_router
from chatbot.features.metrics.scheduler import start_metrics_scheduler
from chatbot.platform.config import get_settings
from chatbot.platform.logger import configure_logging
from chatbot.platform.server import create_app


def bootstrap_application() -> FastAPI:
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
    if settings.crm_provider == "zendesk":
        zendesk_client = ZendeskAdapter(settings)
        chat_port = zendesk_client
        ticketing_port = zendesk_client
    else:
        chatwoot_zammad_client = ChatwootZammadAdapter(settings)
        chat_port = chatwoot_zammad_client
        ticketing_port = chatwoot_zammad_client

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

    # --- Human-agent bridge (Sunshine Conversations) ---
    # Only wired when the credentials are present so dev environments without
    # SC keys still boot.
    human_agent_bridge: HumanAgentBridgePort | None = None
    handoff_bridge: HandoffBridge | None = None
    if settings.zendesk_app_id and settings.zendesk_key_id and settings.zendesk_secret_key:
        human_agent_bridge = SunshineConversationsAdapter(settings)
        handoff_bridge = HandoffBridge(store=build_handoff_store(settings))

    # --- Conversation capture (per-conversation Zendesk logging) ---
    conversation_log_port: ConversationLogPort = (
        zendesk_client if zendesk_client is not None else NoOpConversationLog()
    )

    # --- Twilio channel (outbound WhatsApp) ---
    twilio_adapter: TwilioChannelAdapter | None = None
    if settings.twilio_account_sid and settings.twilio_auth_token:
        twilio_adapter = TwilioChannelAdapter(settings)

    # --- Metrics port (per-turn BigQuery streaming) ---
    metrics_port = build_metrics_port(settings)

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
        )
    )

    qa_port = build_qa_label_port(settings)
    app.include_router(build_qa_router(qa_port, settings))

    @app.get("/")
    def health_check() -> dict[str, str]:
        return {
            "status": "healthy",
            "crm_provider": settings.crm_provider,
            "voice_provider": settings.voice_provider,
            "model": settings.gemini_model,
        }

    metrics_scheduler = start_metrics_scheduler(settings)
    if metrics_scheduler is not None:

        @app.on_event("shutdown")
        def _stop_metrics_scheduler() -> None:
            metrics_scheduler.shutdown(wait=False)

    return app


app = bootstrap_application()
