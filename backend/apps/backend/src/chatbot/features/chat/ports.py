from __future__ import annotations

from dataclasses import dataclass, field
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
    """Port interface for ticketing and customer escalation systems (Chatwoot, Zendesk Support)."""

    async def create_ticket(
        self,
        session_id: str,
        title: str,
        body: str,
        urgency: str,
        customer_name: str | None = None,
        customer_email: str | None = None,
        customer_phone: str | None = None,
        category: str | None = None,
        subcategory: str | None = None,
        case_type: str | None = None,
        vehicle_model: str | None = None,
        division: str | None = None,
        department: str | None = None,
        sla_minutes: int | None = None,
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


@dataclass(frozen=True)
class LiveFaqEntry:
    """A CRM-authored FAQ entry with a precomputed semantic embedding.

    The CRM team edits these in real time via the admin router; the embedding
    is (re)computed on create/update so `/kb/suggest` can match by meaning.
    """

    id: str
    question: str
    answer: str
    keywords: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    embedding: list[float] = field(default_factory=list)
    active: bool = True
    updated_at: str = ""


class LiveFaqPort(Protocol):
    """Port for the real-time, CRM-editable FAQ store with semantic search.

    Writes recompute the entry embedding (via an injected Embedder); reads for
    `search` operate over the currently-active entries so CRM edits reflect
    instantly. Implementations must degrade cleanly (return empty / log) when
    the backing store or embedder is unavailable — never break `/kb/suggest`.
    """

    async def create(self, entry: LiveFaqEntry) -> str:
        """Persist a new entry (computing its embedding). Returns the new id."""
        ...

    async def update(self, entry_id: str, fields: dict[str, Any]) -> None:
        """Patch an entry; recompute the embedding if question/answer changed."""
        ...

    async def delete(self, entry_id: str) -> None:
        """Remove an entry."""
        ...

    async def list_all(self) -> list[LiveFaqEntry]:
        """Return all entries (active + inactive) for the admin listing."""
        ...

    async def list_active(self) -> list[LiveFaqEntry]:
        """Return only active entries (search corpus)."""
        ...

    async def search(
        self, query_embedding: list[float], limit: int
    ) -> list[tuple[LiveFaqEntry, float]]:
        """Cosine-similarity search over active entries. Returns top-`limit`
        `(entry, score)` pairs above a small score floor, best first."""
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

    async def find_conversation_ticket(self, session_id: str) -> str | None:
        """Resolve an EXISTING ticket for this session, in ANY status
        (unlike ``ensure_conversation_ticket``'s reuse check, which is
        active-only) -- and, critically, NEVER create one. For callers that
        must attach data to a conversation that should already exist (e.g.
        a Twilio recording-status callback arriving after the call, and
        after the conversation may have been resolved) rather than a
        customer message that's allowed to open a fresh thread. Returns
        None when nothing matches, so the caller can ignore/skip rather
        than risk creating an empty, orphaned conversation. Must never
        raise."""
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

    async def has_ticket_tag(self, ticket_id: str, tag: str) -> bool:
        """Return whether `tag` is already on the ticket. Lets a caller
        (e.g. `/webhooks/phone/dial-status`) make an otherwise-append-only
        write idempotent against a redelivered webhook without a separate
        dedupe store: skip the write when this is already True. Must
        never raise; fails to `False` (assume not tagged) on any read
        failure, matching this port's fail-open convention."""
        ...

    async def post_public_reply(self, ticket_id: str, text: str, status: str | None = None) -> None:
        """Post a PUBLIC comment to an existing ticket (emailed to the requester
        by Zendesk), optionally setting ticket status."""
        ...

    async def set_ticket_external_id(self, ticket_id: str, external_id: str) -> None:
        """Set the ticket's external_id so status-change triggers can route by session."""
        ...

    async def set_ticket_classification(
        self,
        ticket_id: str,
        *,
        case_type: str | None = None,
        division: str | None = None,
        concern: str | None = None,
        sentiment: str | None = None,
    ) -> None:
        """Best-effort: write AI-derived case_type/division/concern/sentiment
        as conversation custom attributes, using the exact field names the
        Cases List UI and ``features.metrics.mapping`` already read. Only
        the provided (non-None) values are written; must never raise --
        callers treat this as fire-and-forget, same as the rest of this
        port's surface.

        ``sentiment`` (P7 task 1): the per-turn classified level (positive/
        neutral/negative/urgent), so it reaches BigQuery via the existing
        mapping. Written by ``OrchestratorService.capture_conversation``,
        distinct from the case_type/division/concern trio's phone-bridge
        caller -- share the method rather than adding new Chatwoot API
        surface for a single extra attribute."""
        ...

    async def get_latest_public_comment(self, ticket_id: str) -> tuple[str, str | None, str | None]:
        """Fetch the requester's most recent PUBLIC comment body plus their
        name and email. Returns ("", None, None) if none/failure."""
        ...

    async def set_call_recording(
        self,
        ticket_id: str,
        *,
        recording_sid: str,
        recording_duration: str,
        recording_url: str,
    ) -> None:
        """Best-effort: write a Twilio call recording's sid/duration/url as
        INTERNAL conversation custom attributes -- never as a comment/note,
        so a raw Twilio media URL never lands in agent-visible conversation
        text (retrieval is meant to be gated behind a permission; see
        features/authz/seed.py's `call_recording.listen`). Called from the
        `/webhooks/phone/recording-status` callback, once per "completed"
        delivery; implementations should make this a plain attribute SET
        (not append), so a retried callback delivery is naturally
        idempotent. Must never raise -- callers treat this as fire-and-
        forget, same as the rest of this port's surface."""
        ...

    async def get_inbox_working_hours(self, inbox_id: int) -> dict[str, Any] | None:
        """Return the raw Chatwoot inbox record (including its
        ``working_hours``/``working_hours_enabled``/``timezone`` fields) for
        the given inbox id, or ``None`` on any failure (not enabled,
        unreachable, unknown inbox). Used by ``features.chat.phone.
        handoff_target.HandoffTargetResolver`` to gate a live-call transfer
        on business hours via ``features.metrics.business_hours.
        working_minutes_between`` -- the SAME row shape and helper the
        BigQuery ETL already uses, deliberately not a second parser. Must
        never raise; callers fail OPEN (treat "now" as within hours) on
        ``None``, same as that helper's own "not configured -> always open"
        default."""
        ...


class MetricsPort(Protocol):
    """Port for emitting one analytics event per conversational turn."""

    async def emit_turn(self, event: TurnEvent) -> None:
        """Best-effort: record a single turn event. Must never raise."""
        ...


@dataclass(frozen=True)
class AuditEntry:
    """Append-only case status audit trail entry."""

    ticket_id: str
    session_id: str
    actor: str
    from_state: str
    to_state: str
    at: str
    remark: str
    # P2: what actually went out, so "we escalated it" can be checked rather
    # than assumed. All nullable -- every row written before P2 has none of
    # them, and most transitions (a status change, a first response) are not
    # deliveries at all.
    recipients: list[str] | None = None
    transport: str | None = None
    delivery_status: str | None = None
    sla_status: str | None = None


class AuditLogPort(Protocol):
    """Append-only case status audit trail (agent / from→to / time / remark)."""

    async def append(self, entry: AuditEntry) -> None: ...

    async def list_for_ticket(self, ticket_id: str) -> list[AuditEntry]: ...

    async def list_filtered(
        self,
        *,
        actor: str | None = None,
        from_ts: str | None = None,
        to_ts: str | None = None,
        limit: int = 200,
    ) -> list[AuditEntry]: ...
