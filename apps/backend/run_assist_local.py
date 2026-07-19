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

from chatbot.features.assist.router import build_assist_router
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
        allow_methods=["POST", "OPTIONS"],
        allow_headers=["x-api-key", "content-type"],
    )

    app.include_router(
        build_assist_router(settings, _StubKnowledge(), _build_genai_client(settings))
    )

    @app.get("/healthz")
    def healthz() -> dict[str, object]:
        return {"ok": True, "model": settings.assist_gemini_model, "cors": origins}

    return app


if __name__ == "__main__":
    uvicorn.run(build(), host="0.0.0.0", port=8000, log_level="info")
