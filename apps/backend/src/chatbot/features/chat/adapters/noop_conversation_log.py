from __future__ import annotations

from chatbot.features.chat.ports import ConversationLogPort


class NoOpConversationLog(ConversationLogPort):
    """Used when no support system is configured — conversation capture is skipped."""

    async def ensure_conversation_ticket(
        self,
        session_id: str,
        subject: str,
        customer_name: str | None,
        customer_phone: str | None,
    ) -> str:
        return ""

    async def append_conversation_comment(
        self, ticket_id: str, text: str, status: str | None = None
    ) -> None:
        return None
