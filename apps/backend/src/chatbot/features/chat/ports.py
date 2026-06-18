from __future__ import annotations

from typing import Protocol

from chatbot.features.chat.models import (
    AgentMessageEvent,
    HandoffOpenPayload,
    KbArticle,
)


class ChatPort(Protocol):
    """Port interface for sending messages back to the customer's chat interface."""

    async def send_message(self, conversation_id: str, text: str) -> None:
        """Send a plain text response back to the customer."""
        ...


class TicketingPort(Protocol):
    """Port interface for ticketing and customer escalation systems (Zammad, Zendesk Support)."""

    async def create_ticket(self, session_id: str, title: str, body: str, urgency: str) -> str:
        """Create a new customer ticket. Returns the created ticket's ID."""
        ...

    async def add_private_note(self, ticket_id: str, text: str) -> None:
        """Post a private note or internal comment (context banner) in the ticket."""
        ...

    async def pause_ai_for_session(self, session_id: str) -> None:
        """Pause AI response actions on this session due to handoff."""
        ...

    async def unpause_ai_for_session(self, session_id: str) -> None:
        """Resume AI response actions on this session."""
        ...

    async def is_ai_paused(self, session_id: str) -> bool:
        """Check if AI is paused for this session (e.g. human is currently handling it)."""
        ...


class KnowledgePort(Protocol):
    """Port interface for retrieving articles from a Knowledge Base (Zendesk Guide, DB)."""

    async def search_kb(self, query: str, limit: int = 2) -> list[KbArticle]:
        """Search documentation articles matching query."""
        ...


class TextToSpeechPort(Protocol):
    """Port interface for synthesizing text into audio speech via Gemini TTS."""

    async def synthesize(self, text: str, language_code: str = "en-US") -> bytes:
        """Synthesize text into MP3 audio bytes."""
        ...


class HumanAgentBridgePort(Protocol):
    """Port for relaying customer↔agent messages through an external messaging
    platform (Sunshine Conversations). The platform takes over the conversation
    after AI handoff; we use it as the transport so the customer can keep
    talking in our own UI while a Zendesk agent replies from their workspace.
    """

    async def open_handoff(self, payload: HandoffOpenPayload) -> str:
        """Create a conversation in the external platform with the AI summary
        and recent transcript preloaded as a business message. Returns the
        platform's conversation_id."""
        ...

    async def forward_customer_message(
        self, conversation_id: str, user_external_id: str, text: str
    ) -> None:
        """Post a customer-authored message into the external conversation."""
        ...

    def verify_webhook_signature(self, body: bytes, signature: str | None) -> bool:
        """Verify an inbound webhook request's authenticity."""
        ...

    def parse_webhook_events(self, payload: dict[str, object]) -> list[AgentMessageEvent]:
        """Extract agent-authored message events from a webhook body."""
        ...
