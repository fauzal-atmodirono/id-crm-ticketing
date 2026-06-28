from __future__ import annotations

from chatbot.features.chat.ports import ConversationLogPort


class NoOpConversationLog(ConversationLogPort):
    """Used when no support system is configured — conversation capture is skipped."""

    async def ensure_conversation_ticket(
        self,
        session_id: str,  # noqa: ARG002
        subject: str,  # noqa: ARG002
        customer_name: str | None,  # noqa: ARG002
        customer_phone: str | None,  # noqa: ARG002
    ) -> str:
        return ""

    async def append_conversation_comment(
        self,
        ticket_id: str,  # noqa: ARG002
        text: str,  # noqa: ARG002
        status: str | None = None,  # noqa: ARG002
    ) -> None:
        return None

    async def add_ticket_tag(self, ticket_id: str, tag: str) -> None:  # noqa: ARG002
        return None

    async def post_public_reply(
        self,
        ticket_id: str,  # noqa: ARG002
        text: str,  # noqa: ARG002
        status: str | None = None,  # noqa: ARG002
    ) -> None:
        return None

    async def set_ticket_external_id(
        self,
        ticket_id: str,  # noqa: ARG002
        external_id: str,  # noqa: ARG002
    ) -> None:
        return None
