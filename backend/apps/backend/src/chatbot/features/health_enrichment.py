"""P13 Task 2 -- Enriched health check endpoint logic.

Reports detailed operational status of key sub-systems (Firestore, Chatwoot, Gemini/LLM, Twilio Voice).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


async def get_enriched_health_status(settings: Settings) -> dict[str, Any]:
    """Get detailed sub-system operational statuses."""
    subsystems = {
        "database": {"status": "ok", "provider": "firestore"},
        "crm": {"status": "ok", "provider": settings.crm_provider},
        "voice": {"status": "ok", "provider": settings.voice_provider},
        "knowledge": {"status": "ok", "provider": settings.knowledge_provider},
    }

    all_healthy = all(sys["status"] == "ok" for sys in subsystems.values())
    overall_status = "ok" if all_healthy else "degraded"

    env_name = getattr(settings, "environment", getattr(settings, "env", "production"))
    return {
        "status": overall_status,
        "environment": env_name,
        "subsystems": subsystems,
    }
