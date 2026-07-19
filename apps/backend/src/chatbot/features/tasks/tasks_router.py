"""GET /tasks/mine — per-agent open tasks with SLA deadlines.

Fetches all conversations from Chatwoot via the existing ``fetch_conversations``
pipeline, optionally filters to a specific ``agent_id``, computes deadlines with
``compute_deadlines``, and returns a JSON payload the My-Tasks app polls.

Query params:
  agent_id (optional) — Chatwoot agent numeric id as a string. When omitted,
  returns ALL non-resolved conversations (useful for supervisor views).

Response (200):
  {
    "tasks": [
      {
        "convId": "123",
        "subject": "CustomerName",
        "status": "open",
        "agentId": "7",
        "createdAtIso": "2026-07-18T02:00:00+00:00",
        "responseDeadlineIso": "2026-07-18T10:00:00+00:00",  // null if replied
        "resolutionDeadlineIso": "2026-07-20T02:00:00+00:00",
        "responseRemainingSeconds": 18000.0,  // negative = breached; null if replied
        "resolutionRemainingSeconds": 86400.0,  // negative = breached
        "breachType": null  // "NO_RESPONSE" | "UNRESOLVED" | null
      }
    ],
    "generatedAt": "2026-07-18T10:00:00+00:00",
    "warningThresholdMinutes": 60
  }
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from chatbot.features.metrics.sync import fetch_conversations
from chatbot.features.tasks.deadline import TaskItem, compute_deadlines

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

_ACTIVE_STATUSES = frozenset({"open", "pending", "snoozed"})


def _utcnow() -> datetime:
    """Replaceable clock for tests."""
    return datetime.now(UTC)


def _task_item_to_dict(item: TaskItem) -> dict[str, Any]:
    return {
        "convId": item.conv_id,
        "subject": item.subject,
        "status": item.status,
        "agentId": item.agent_id,
        "createdAtIso": item.created_at_iso,
        "responseDeadlineIso": item.response_deadline_iso,
        "resolutionDeadlineIso": item.resolution_deadline_iso,
        "responseRemainingSeconds": item.response_remaining_seconds,
        "resolutionRemainingSeconds": item.resolution_remaining_seconds,
        "breachType": item.breach_type,
    }


def build_tasks_router(settings: "Settings") -> APIRouter:
    router = APIRouter()

    @router.get("/tasks/mine")
    async def get_my_tasks(
        agent_id: str | None = Query(default=None, description="Chatwoot agent id to filter"),
    ) -> JSONResponse:
        now = _utcnow()
        try:
            conversations = fetch_conversations(settings)
        except Exception as e:
            _log.error("tasks_fetch_failed", error=str(e))
            return JSONResponse(
                status_code=502,
                content={"error": "Failed to fetch conversations from Chatwoot"},
            )

        tasks: list[dict[str, Any]] = []
        for conv in conversations:
            status = str(conv.get("status") or "")
            if status not in _ACTIVE_STATUSES:
                continue
            item = compute_deadlines(conv, settings, now)
            if agent_id is not None and item.agent_id != agent_id:
                continue
            tasks.append(_task_item_to_dict(item))

        # Sort: breached first, then by resolution_remaining_seconds ascending
        tasks.sort(
            key=lambda t: (
                t["breachType"] is None,  # breached items sort first (False < True)
                t["resolutionRemainingSeconds"] if t["resolutionRemainingSeconds"] is not None else float("inf"),
            )
        )

        return JSONResponse(
            content={
                "tasks": tasks,
                "generatedAt": now.isoformat(),
                "warningThresholdMinutes": settings.tasks_reminder_warning_minutes,
            }
        )

    return router
