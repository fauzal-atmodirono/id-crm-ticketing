from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


def create_app(settings: Settings) -> FastAPI:
    """FastAPI ASGI app factory."""
    app = FastAPI(
        title="Proton Conversational AI Backend",
        description=(
            "Conversational Chatbot and Voicebot API gateway "
            "built on FastAPI, Gemini, and CRM adapters."
        ),
        version="0.1.0",
        debug=settings.debug,
    )

    # Configure CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Observability Middleware: Logs incoming requests, attaches Correlation ID, measures duration
    @app.middleware("http")
    async def log_request_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Generate correlation ID if not present
        correlation_id = request.headers.get("X-Correlation-ID") or str(uuid.uuid4())
        structlog.contextvars.bind_contextvars(correlation_id=correlation_id)

        start_time = time.perf_counter()

        _log.info(
            "request_started",
            method=request.method,
            path=request.url.path,
            client=request.client.host if request.client else None,
        )

        response = await call_next(request)

        duration = (time.perf_counter() - start_time) * 1000.0
        response.headers["X-Correlation-ID"] = correlation_id

        _log.info(
            "request_completed",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=round(duration, 2),
        )

        return response

    return app
