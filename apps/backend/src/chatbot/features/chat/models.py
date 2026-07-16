from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

Language = Literal["en", "ms", "zh", "unknown"]
Sentiment = Literal["positive", "neutral", "negative"]
HandoffReason = Literal[
    "negative_sentiment", "keyword", "help_request", "unknown_retry_limit", "sales_lead"
]


@dataclass(frozen=True)
class Message:
    """Represents a single message in a conversation turn."""

    role: Literal["user", "assistant", "system"]
    text: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class HandoffPayload:
    """Context details generated when an escalation to a human agent is triggered."""

    reason: HandoffReason
    language: Language
    summary: str | None = None
    urgency: Literal["low", "medium", "high"] = "medium"
    # True if the Sunshine Conversations live bridge opened successfully and
    # the frontend can subscribe to /chat/stream for inline agent messages.
    # False means the session is paused but no live-chat channel is available
    # (agent will reply via the Support ticket asynchronously).
    live_chat_available: bool = False
    lead_details: dict[str, Any] | None = None
    classification: dict[str, Any] | None = None


@dataclass(frozen=True)
class MediaAttachment:
    """Attachments associated with message turns (e.g. catalog images)."""

    kind: str
    content_type: str
    url: str
    storage_ref: str | None = None


@dataclass(frozen=True)
class ProductCard:
    """A single product/model card for the A2UI-shaped carousel block."""

    title: str
    description: str
    image_url: str | None = None
    price: str | None = None
    url: str | None = None


@dataclass(frozen=True)
class TurnResult:
    """Consolidated response parameters generated after running the Gemini turn."""

    reply: str | None
    language: Language = "unknown"
    sentiment: Sentiment | None = None
    handoff: HandoffPayload | None = None
    attachments: list[MediaAttachment] = field(default_factory=list)
    products: list[ProductCard] = field(default_factory=list)
    # Set when the session has already been handed off and this turn's text
    # was relayed to the human-agent bridge instead of Gemini. The reply
    # arrives asynchronously over /chat/stream/{session_id}, not in this call.
    forwarded_to_agent: bool = False
    # Set if the turn involved transcribing user voice audio (transcription text)
    user_transcription: str | None = None


@dataclass(frozen=True)
class KbArticle:
    """A single article retrieved from the Knowledge Base."""

    title: str
    content: str
    url: str | None = None
    source_type: str | None = None
    price: str | None = None
    image_urls: list[str] = field(default_factory=list)
    brochure_url: str | None = None


@dataclass(frozen=True)
class HandoffOpenPayload:
    """Context handed to HumanAgentBridgePort.open_handoff."""

    session_id: str
    customer_name: str
    customer_email: str
    ai_summary: str
    transcript: tuple[Message, ...]
    urgency: Literal["low", "medium", "high"] = "medium"
    language: Language = "unknown"
    customer_phone: str | None = None
    preferred_model: str | None = None
    # AI classification carried into the handoff so the bridge can persist it on
    # the ticket/conversation for the downstream metrics sync to read back.
    category: str | None = None
    subcategory: str | None = None
    division: str | None = None
    department: str | None = None
    sla_minutes: int | None = None


@dataclass(frozen=True)
class AgentMessageEvent:
    """A normalized human-agent message extracted from a bridge webhook."""

    conversation_id: str
    author_name: str
    text: str
    timestamp: datetime
