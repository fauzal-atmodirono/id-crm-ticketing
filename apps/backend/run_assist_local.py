"""Minimal local runner for the /assist/* endpoints — LOCAL TESTING ONLY.

Mounts only `build_assist_router` (Suggest / Summarize / Ask) instead of booting
the full GCP-heavy application, so we can smoke-test the patched Chatwoot frontend
end-to-end. Gemini uses the same Vertex/ADC config as the real app (read from
.env); the KB is stubbed so no Vertex Search data store is required.

Run from apps/backend:
    PROTON_BACKEND_KEY=local-test-key \
    .venv/bin/python run_assist_local.py
Serves on http://0.0.0.0:8000.
"""

from __future__ import annotations

import os

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from chatbot.features.assist.copilot_router import build_copilot_router
from chatbot.features.assist.router import build_assist_router
from chatbot.features.chat.adapters.assistants_store import build_assistants_store
from chatbot.features.chat.adapters.chatwoot import ChatwootAdapter
from chatbot.features.chat.adapters.inbox_assignment_store import build_inbox_assignment_store
from chatbot.features.chat.adapters.live_faq import InMemoryLiveFaqStore, VertexEmbedder
from chatbot.features.chat.adapters.merged_knowledge import MergedKnowledgeAdapter
from chatbot.features.chat.adapters.scenarios_store import build_scenarios_store
from chatbot.features.chat.adapters.tenant_settings_store import build_tenant_settings_store
from chatbot.features.chat.adapters.tools_store import build_tools_store
from chatbot.features.chat.faq_admin_router import build_faq_admin_router
from chatbot.features.chat.kb_assistants_router import build_kb_assistants_router
from chatbot.features.chat.kb_documents_router import build_kb_documents_router
from chatbot.features.chat.kb_inboxes_router import build_kb_inboxes_router
from chatbot.features.chat.kb_scenarios_router import build_kb_scenarios_router
from chatbot.features.chat.kb_settings_router import build_kb_settings_router
from chatbot.features.chat.kb_tools_router import build_kb_tools_router
from chatbot.features.chat.models import KbArticle
from chatbot.platform.config import get_settings


class _StubKnowledge:
    """Returns no KB hits — /suggest and /ask still work, just ungrounded.

    Swap the return for a canned KbArticle to see grounding/sources in the
    response, e.g. [KbArticle(title="Warranty", content="...", url="http://faq/1")].
    """

    async def search_kb(self, query: str, limit: int = 2) -> list[KbArticle]:  # noqa: ARG002
        return []


def _build_genai_client(settings):  # type: ignore[no-untyped-def]
    from google.genai import Client

    if settings.google_genai_use_vertexai:
        return Client(
            vertexai=True,
            project=settings.vertex_project_id,
            location=settings.vertex_location,
        )
    return Client()


def build() -> FastAPI:
    settings = get_settings()
    if not settings.proton_backend_key:
        raise SystemExit(
            "PROTON_BACKEND_KEY is empty — set it (env or .env) so /assist/* is authorized."
        )

    app = FastAPI(title="proton-assist-local")

    # Allow the local Chatwoot origin(s) to call /assist/* cross-origin.
    origins = [
        o.strip()
        for o in os.environ.get(
            "ASSIST_LOCAL_ORIGINS",
            "http://crm.127-0-0-1.nip.io,http://localhost:3000",
        ).split(",")
        if o.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["x-api-key", "content-type"],
    )

    genai = _build_genai_client(settings)
    embedder = VertexEmbedder(genai, settings.embedding_model) if genai is not None else None
    live_store = InMemoryLiveFaqStore(embedder) if embedder is not None else None
    kp = MergedKnowledgeAdapter(_StubKnowledge(), live_store, embedder)

    app.include_router(build_faq_admin_router(live_store, settings))
    app.include_router(build_kb_documents_router(settings))
    assistants_store = build_assistants_store(settings)
    tenant_settings_store = build_tenant_settings_store(settings)
    tools_store = build_tools_store(settings)
    scenarios_store = build_scenarios_store(settings)
    assignment_store = build_inbox_assignment_store(settings)
    app.include_router(
        build_kb_assistants_router(
            assistants_store,
            settings,
            scenarios_store=scenarios_store,
            assignment_store=assignment_store,
        )
    )
    app.include_router(build_kb_settings_router(tenant_settings_store, settings))
    app.include_router(build_kb_tools_router(tools_store, settings))
    app.include_router(build_kb_scenarios_router(scenarios_store, tools_store, settings))
    chatwoot_adapter = ChatwootAdapter(settings)
    app.include_router(
        build_kb_inboxes_router(
            assignment_store, assistants_store, tenant_settings_store, chatwoot_adapter, settings
        )
    )
    app.include_router(
        build_assist_router(settings, kp, genai)
    )
    app.include_router(
        build_copilot_router(
            settings,
            kp,
            genai,
            assistants_store,
            tenant_settings_store,
            tools_store=tools_store,
            scenarios_store=scenarios_store,
            assignment_store=assignment_store,
        )
    )

    @app.get("/healthz")
    def healthz() -> dict[str, object]:
        return {"ok": True, "model": settings.assist_gemini_model, "cors": origins}

    return app


if __name__ == "__main__":
    uvicorn.run(build(), host="0.0.0.0", port=8000, log_level="info")
