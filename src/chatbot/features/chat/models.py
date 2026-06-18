from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

Language = Literal["en", "ms", "zh", "unknown"]
Sentiment = Literal["positive", "neutral", "negative"]
HandoffReason = Literal["negative_sentiment", "keyword", "help_request", "unknown_retry_limit"]


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


@dataclass(frozen=True)
class MediaAttachment:
    """Attachments associated with message turns (e.g. catalog images)."""

    kind: str
    content_type: str
    url: str
    storage_ref: str | None = None


@dataclass(frozen=True)
class TurnResult:
    """Consolidated response parameters generated after running the Gemini turn."""

    reply: str | None
    language: Language = "unknown"
    sentiment: Sentiment | None = None
    handoff: HandoffPayload | None = None
    attachments: list[MediaAttachment] = field(default_factory=list)


@dataclass(frozen=True)
class KbArticle:
    """A single article retrieved from the Knowledge Base."""

    title: str
    content: str
    url: str | None = None


@dataclass(frozen=True)
class VoiceTranscript:
    """Result of Speech-to-Text transcription."""

    text: str
    confidence: float
    language: str
    duration_ms: int
