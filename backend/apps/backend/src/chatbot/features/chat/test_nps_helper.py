from __future__ import annotations

from unittest.mock import AsyncMock

from chatbot.features.chat.nps import record_nps_on_ticket


async def test_record_nps_on_ticket_posts_comment_and_tag() -> None:
    port = AsyncMock()
    await record_nps_on_ticket(port, "T-7", 9, "web")
    port.append_conversation_comment.assert_awaited_once_with(
        "T-7", "📣 Net Promoter Score: 9/10 (via web)"
    )
    port.add_ticket_tag.assert_awaited_once_with("T-7", "nps_9")


async def test_record_nps_on_ticket_swallows_errors() -> None:
    port = AsyncMock()
    port.append_conversation_comment.side_effect = RuntimeError("zendesk down")
    # must not raise
    await record_nps_on_ticket(port, "T-7", 3, "web")
