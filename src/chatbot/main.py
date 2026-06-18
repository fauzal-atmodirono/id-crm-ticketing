from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from chatbot.features.chat.adapters.chatwoot_zammad import ChatwootZammadAdapter
from chatbot.features.chat.adapters.gcp_voice import GcpSpeechToTextAdapter, GcpTextToSpeechAdapter
from chatbot.features.chat.adapters.mock import (
    InMemoryKnowledgeAdapter,
    MockVoiceAdapter,
)
from chatbot.features.chat.adapters.zendesk import ZendeskAdapter
from chatbot.features.chat.ports import (
    ChatPort,
    KnowledgePort,
    SpeechToTextPort,
    TextToSpeechPort,
    TicketingPort,
)
from chatbot.features.chat.router import build_chat_router
from chatbot.features.chat.service import OrchestratorService
from chatbot.platform.config import get_settings
from chatbot.platform.logger import configure_logging
from chatbot.platform.server import create_app


def bootstrap_application() -> FastAPI:
    """Bootstrap settings, configure structured loggers, wire adapters, and return app."""
    # 1. Load application configurations
    settings = get_settings()

    # 2. Setup structured logging
    configure_logging(settings.debug)

    # 3. Create FastAPI app
    app = create_app(settings)

    # 4. Wire Adapters based on the configuration provider switches
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
        # Fallback to local Chatwoot & Zammad open-source simulator
        chatwoot_zammad_client = ChatwootZammadAdapter(settings)
        chat_port = chatwoot_zammad_client
        ticketing_port = chatwoot_zammad_client
        # Zammad does not serve a public KB directly, so we use the local in-memory KB
        knowledge_port = InMemoryKnowledgeAdapter()

    # --- Voice Processing wiring ---
    stt_port: SpeechToTextPort
    tts_port: TextToSpeechPort

    if settings.voice_provider == "gcp":
        stt_port = GcpSpeechToTextAdapter()
        tts_port = GcpTextToSpeechAdapter()
    else:
        # Mock voice processors
        voice_client = MockVoiceAdapter()
        stt_port = voice_client
        tts_port = voice_client

    # 5. Instantiate Unified Orchestrator Service
    orchestrator = OrchestratorService(
        settings=settings,
        chat_port=chat_port,
        ticketing_port=ticketing_port,
        knowledge_port=knowledge_port,
        stt_port=stt_port,
        tts_port=tts_port,
    )

    # 6. Include endpoints and mount static folders for voice file playbacks
    app.include_router(build_chat_router(orchestrator))

    os.makedirs("static/audio", exist_ok=True)
    app.mount("/static", StaticFiles(directory="static"), name="static")

    # Simple root health check endpoint
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
