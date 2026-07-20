"""Shared NPS recording: the channel-agnostic Zendesk write (comment + tag).

Mirrors csat.py. Posts the identical `nps_<score>` tag + comment that the
metrics dashboard's Phase-1 sync reads (parsed into the conversations.nps_score
column and surfaced via the v_nps view).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from chatbot.features.chat.ports import ConversationLogPort

_log = structlog.get_logger(__name__)


async def record_nps_on_ticket(
    port: ConversationLogPort, ticket_id: str, score: int, channel: str
) -> None:
    """Post the NPS comment + `nps_<score>` tag to a ticket. Best-effort."""
    try:
        await port.append_conversation_comment(
            ticket_id, f"📣 Net Promoter Score: {score}/10 (via {channel})"
        )
        await port.add_ticket_tag(ticket_id, f"nps_{score}")
    except Exception as e:
        _log.error("record_nps_on_ticket_failed", ticket_id=ticket_id, error=str(e))
