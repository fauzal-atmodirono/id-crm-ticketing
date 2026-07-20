"""Shared CSAT recording: the channel-agnostic Zendesk write (comment + tag).

Used by both OrchestratorService.record_csat (text/WhatsApp/email/web) and the
phone bridge, so every channel emits the identical `csat_<score>` tag + comment
that the future metrics dashboard reads.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from chatbot.features.chat.ports import ConversationLogPort

_log = structlog.get_logger(__name__)


async def record_csat_on_ticket(
    port: ConversationLogPort, ticket_id: str, score: int, channel: str
) -> None:
    """Post the CSAT comment + `csat_<score>` tag to a ticket. Best-effort."""
    try:
        await port.append_conversation_comment(
            ticket_id, f"⭐ Customer satisfaction: {score}/5 (via {channel})"
        )
        await port.add_ticket_tag(ticket_id, f"csat_{score}")
    except Exception as e:
        _log.error("record_csat_on_ticket_failed", ticket_id=ticket_id, error=str(e))
