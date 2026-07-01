from __future__ import annotations

from enum import StrEnum
from typing import Any, Protocol

from chatbot.features.chat.models import (
    AgentMessageEvent,
    HandoffOpenPayload,
    KbArticle,
)
from chatbot.features.metrics.events import TurnEvent


class ConversationLogResult(StrEnum):
    """Outcome of appending a comment to a conversation ticket.

    Lets the caller distinguish a ticket that can no longer accept comments
    (closed/deleted → rotate to a fresh ticket) from a transient failure
    (retry later; do NOT spawn a replacement ticket).
    """

    OK = "ok"
    TICKET_CLOSED = "ticket_closed"
    FAILED = "failed"


class ChatPort(Protocol):
    """Port interface for sending messages back to the customer's chat interface."""

    async def send_message(self, conversation_id: str, text: str) -> None:
        """Send a plain text response back to the customer."""
        ...


class TicketingPort(Protocol):
    """Port interface for ticketing and customer escalation systems (Zammad, Zendesk Support)."""

    async def create_ticket(
        self,
        session_id: str,
        title: str,
        body: str,
        urgency: str,
        customer_name: str | None = None,
        customer_email: str | None = None,
        customer_phone: str | None = None,
    ) -> str:
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


class HandoffStorePort(Protocol):
    """Persistent backing store for the `session_id ↔ conversation_id` mapping
    that survives backend restarts. In-process subscribers (asyncio queues) live
    only in memory and are restored when clients reconnect their SSE streams.
    """

    async def register(
        self,
        session_id: str,
        conversation_id: str,
        transcript: list[dict[str, Any]] | None = None,
    ) -> None:
        """Persist a new handoff, optionally including the initial conversation transcript."""
        ...

    async def save_message(self, session_id: str, role: str, text: str) -> None:
        """Append a message to the stored conversation transcript."""
        ...

    async def get_conversation_id(self, session_id: str) -> str | None:
        """Look up the conversation associated with a session."""
        ...

    async def get_session_id(self, conversation_id: str) -> str | None:
        """Reverse lookup: which session owns this conversation."""
        ...

    async def unregister(self, session_id: str) -> None:
        """Remove a handoff (e.g. after the customer clicks New Session)."""
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


class ConversationLogPort(Protocol):
    """Port for mirroring a full conversation into the support system as a ticket.

    Distinct from TicketingPort.create_ticket (which is escalation-shaped): this
    is per-conversation capture, deciding open ticket vs solved log via the
    detection gate.
    """

    async def ensure_conversation_ticket(
        self,
        session_id: str,
        subject: str,
        customer_name: str | None,
        customer_phone: str | None,
    ) -> str:
        """Find-or-create the conversation's ticket (external_id == session_id).
        Returns the ticket id."""
        ...

    async def rotate_conversation_ticket(
        self,
        session_id: str,
        subject: str,
        customer_name: str | None,
        customer_phone: str | None,
    ) -> str:
        """Force-create a FRESH conversation ticket, bypassing any cache, and make
        it the current one for the session. Used when the prior ticket is closed
        and can no longer accept comments. Returns the new ticket id."""
        ...

    async def append_conversation_comment(
        self, ticket_id: str, text: str, status: str | None = None
    ) -> ConversationLogResult:
        """Append a private comment, optionally setting ticket status. Returns
        whether it succeeded, or that the ticket can no longer be commented on."""
        ...

    async def add_ticket_tag(self, ticket_id: str, tag: str) -> None:
        """Add a single tag to the ticket (additive; does not replace tags)."""
        ...

    async def post_public_reply(self, ticket_id: str, text: str, status: str | None = None) -> None:
        """Post a PUBLIC comment to an existing ticket (emailed to the requester
        by Zendesk), optionally setting ticket status."""
        ...

    async def set_ticket_external_id(self, ticket_id: str, external_id: str) -> None:
        """Set the ticket's external_id so status-change triggers can route by session."""
        ...

    async def get_latest_public_comment(self, ticket_id: str) -> tuple[str, str | None, str | None]:
        """Fetch the requester's most recent PUBLIC comment body plus their
        name and email. Returns ("", None, None) if none/failure."""
        ...


class MetricsPort(Protocol):
    """Port for emitting one analytics event per conversational turn."""

    async def emit_turn(self, event: TurnEvent) -> None:
        """Best-effort: record a single turn event. Must never raise."""
        ...
