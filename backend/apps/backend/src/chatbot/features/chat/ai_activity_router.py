"""AI activity monitor -- ``GET /admin/ai-activity`` (FIX-43, RFP 2026_028
3.2.5: "a Master view/UI of all AI-handled conversations for administrator to
monitor in real time").

Data path (decision recorded in the Task 24 report): the agent service logs
every bot decision to ``ai_actions`` in ITS Postgres, which this backend cannot
read. Rather than add cross-service DB access (or rebuild the agent image),
this router derives the picture from Chatwoot, which is the system of record
for who is handling a conversation:

  * ``status=pending`` conversations are the ones the agent bot is handling
    (the orchestrator only acts on pending conversations).
  * ``status=open`` + unassigned + the ``escalate`` label are conversations the
    AI has handed off and no human has picked up yet.

Each row is enriched with the conversation's last message, classified into a
"last action" (what the AI -- or the customer, if the AI hasn't answered yet --
did last), and with the backend's own pause flag (``is_ai_paused``) so a
conversation a human took over shows as ``ai_paused``.

"Real time" means a poll: the response is cached for ~12 s and the page polls
on the ``refresh`` block it is given -- not a pushed stream.

Fail-open per source: Chatwoot being unreadable for one of the two lists
degrades that list to ``sources[...] == "unavailable"`` and never 500s or
blanks the other. A pause-lookup failure treats the conversation as active.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter, Depends

from chatbot.features.authz.deps import require_permission

if TYPE_CHECKING:
    from chatbot.features.authz.identity import TokenValidator
    from chatbot.features.authz.repository import AuthzRepository
    from chatbot.features.chat.adapters.chatwoot import ChatwootAdapter
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

CACHE_SECONDS = 12.0
_REFRESH_SECONDS = 15
_ESCALATE_LABEL = "escalate"
_PREVIEW_CHARS = 160


def _last_action(conv: dict[str, Any]) -> dict[str, Any] | None:
    msg = conv.get("last_non_activity_message")
    if not isinstance(msg, dict):
        return None
    mtype = msg.get("message_type")
    if mtype in (0, "incoming"):
        kind = "awaiting_ai"
    elif msg.get("private"):
        kind = "ai_suggestion"
    else:
        kind = "ai_reply"
    at = msg.get("created_at")
    return {
        "type": kind,
        "at": datetime.fromtimestamp(at, UTC).isoformat() if isinstance(at, int | float) else None,
        "preview": (msg.get("content") or "")[:_PREVIEW_CHARS],
    }


def _row(conv: dict[str, Any], mode: str) -> dict[str, Any]:
    meta = conv.get("meta") or {}
    sender = meta.get("sender") or {}
    return {
        "conversation_id": conv.get("id"),
        "inbox_id": conv.get("inbox_id"),
        "customer": sender.get("name"),
        "status": conv.get("status"),
        "labels": conv.get("labels") or [],
        "mode": mode,
        "last_action": _last_action(conv),
        "last_activity_at": conv.get("last_activity_at") or conv.get("timestamp"),
    }


def build_ai_activity_router(
    chatwoot: ChatwootAdapter,
    authz_repo: AuthzRepository | None,
    validator: TokenValidator | None,
    settings: Settings,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> APIRouter:
    router = APIRouter(prefix="/admin", tags=["ai-activity"])
    view = require_permission(
        "ai_activity.view", repo=authz_repo, validator=validator, settings=settings
    )
    cache: dict[str, Any] = {"at": None, "body": None}

    async def _fetch(status: str, **kw: Any) -> list[dict[str, Any]] | None:
        try:
            return await chatwoot.list_conversations_by_status(status, **kw)
        except Exception as exc:
            _log.warning("ai_activity_source_failed", status=status, error_type=type(exc).__name__)
            return None

    async def _paused(conv: dict[str, Any]) -> bool:
        sender = (conv.get("meta") or {}).get("sender") or {}
        keys = [str(conv.get("id"))]
        keys += [str(sender[k]) for k in ("identifier", "phone_number") if sender.get(k)]
        try:
            for key in keys:
                if await chatwoot.is_ai_paused(key):
                    return True
        except Exception as exc:
            _log.warning("ai_activity_pause_lookup_failed", error_type=type(exc).__name__)
        return False

    async def _build() -> dict[str, Any]:
        pending, unassigned = await asyncio.gather(
            _fetch("pending"), _fetch("open", assignee_type="unassigned")
        )
        rows: list[dict[str, Any]] = []
        if pending:
            paused = await asyncio.gather(*(_paused(c) for c in pending))
            rows += [
                _row(c, "ai_paused" if p else "ai_active")
                for c, p in zip(pending, paused, strict=True)
            ]
        for c in unassigned or []:
            if _ESCALATE_LABEL in (c.get("labels") or []):
                rows.append(_row(c, "escalated"))
        rows.sort(key=lambda r: r.get("last_activity_at") or 0, reverse=True)
        summary = {
            m: sum(1 for r in rows if r["mode"] == m)
            for m in ("ai_active", "ai_paused", "escalated")
        }
        return {
            "generated_at": datetime.now(UTC).isoformat(),
            "refresh": {
                "recommended_interval_seconds": _REFRESH_SECONDS,
                "note": "Live poll (cached ~12s), not a pushed stream.",
            },
            "sources": {
                "pending": "ok" if pending is not None else "unavailable",
                "escalated": "ok" if unassigned is not None else "unavailable",
            },
            "summary": summary,
            "conversations": rows,
        }

    @router.get("/ai-activity", dependencies=[Depends(view)])
    async def ai_activity() -> dict[str, Any]:
        now = clock()
        if cache["body"] is not None and now - cache["at"] < CACHE_SECONDS:
            return cache["body"]
        body = await _build()
        cache["at"], cache["body"] = now, body
        return body

    return router
