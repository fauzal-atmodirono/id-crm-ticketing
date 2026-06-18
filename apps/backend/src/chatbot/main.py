from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from chatbot.features.chat.adapters.chatwoot_zammad import ChatwootZammadAdapter
from chatbot.features.chat.adapters.gcp_voice import GeminiTextToSpeechAdapter
from chatbot.features.chat.adapters.mock import InMemoryKnowledgeAdapter, MockVoiceAdapter
from chatbot.features.chat.adapters.zendesk import ZendeskAdapter
from chatbot.features.chat.ports import ChatPort, KnowledgePort, TextToSpeechPort, TicketingPort
from chatbot.features.chat.router import build_chat_router
from chatbot.features.chat.service import OrchestratorService
from chatbot.platform.config import get_settings
from chatbot.platform.logger import configure_logging
from chatbot.platform.server import create_app


def bootstrap_application() -> FastAPI:
    """Bootstrap settings, structured logging, adapters, CORS, and routes."""
    settings = get_settings()
    configure_logging(settings.debug)

    app = create_app(settings)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["X-Reply-Text", "X-Handoff-Reason"],
    )

    # --- CRM and Ticketing wiring ---
    chat_port: ChatPort
    ticketing_port: TicketingPort
    knowledge_port: KnowledgePort

    if settings.crm_provider == "zendesk":
        zendesk_client = ZendeskAdapter(settings)
        chat_port = zendesk_client
        ticketing_port = zendesk_client
        knowledge_port = zendesk_client
    else:
        chatwoot_zammad_client = ChatwootZammadAdapter(settings)
        chat_port = chatwoot_zammad_client
        ticketing_port = chatwoot_zammad_client
        knowledge_port = InMemoryKnowledgeAdapter()

    # --- Voice (TTS) wiring — STT is no longer a separate step; Gemini consumes audio natively ---
    tts_port: TextToSpeechPort
    if settings.voice_provider == "gcp":
        tts_port = GeminiTextToSpeechAdapter(settings)
    else:
        tts_port = MockVoiceAdapter()

    orchestrator = OrchestratorService(
        settings=settings,
        chat_port=chat_port,
        ticketing_port=ticketing_port,
        knowledge_port=knowledge_port,
        tts_port=tts_port,
    )

    app.include_router(build_chat_router(orchestrator))

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
