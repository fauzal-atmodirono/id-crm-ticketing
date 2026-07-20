from __future__ import annotations

from unittest.mock import AsyncMock

from chatbot.features.chat.csat import record_csat_on_ticket


async def test_record_csat_on_ticket_posts_comment_and_tag() -> None:
    port = AsyncMock()
    await record_csat_on_ticket(port, "T-7", 5, "phone")
    port.append_conversation_comment.assert_awaited_once_with(
        "T-7", "⭐ Customer satisfaction: 5/5 (via phone)"
    )
    port.add_ticket_tag.assert_awaited_once_with("T-7", "csat_5")


async def test_record_csat_on_ticket_swallows_errors() -> None:
    port = AsyncMock()
    port.append_conversation_comment.side_effect = RuntimeError("zendesk down")
    # must not raise
    await record_csat_on_ticket(port, "T-7", 3, "phone")
