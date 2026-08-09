"""SQLAlchemy 2.0 declarative models for the agent service's own database.

These tables track state the integration layer needs that doesn't belong in
Chatwoot itself: which webhook deliveries have already been processed
(idempotency), a log of AI decisions, and conversation lifecycle state.
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ProcessedDelivery(Base):
    """Records webhook delivery ids already handled, for idempotency."""

    __tablename__ = "processed_deliveries"

    delivery_id: Mapped[str] = mapped_column(Text, primary_key=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AiAction(Base):
    """Log of a decision the AI layer made about a conversation/ticket."""

    __tablename__ = "ai_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_ref: Mapped[str] = mapped_column(Text, nullable=False)
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    # output_tokens/cached_tokens (P8 task 1): the other two of the three
    # token classes `google-genai` reports per call, alongside prompt_tokens
    # above. Nullable for two independent reasons, not just SQLAlchemy
    # convention: (1) `None` here means "not captured" and must stay
    # distinguishable from a genuine `0`-token call, the same rule that
    # governs how `app/ai/gemini.py` extracts them; (2) an existing row
    # predates these columns and can never retroactively know its own
    # counts. Because this repo has no Alembic/migrations (schema is created
    # via `Base.metadata.create_all` in `init_db`), these columns exist on a
    # freshly created database but do NOT retroactively appear on an
    # already-deployed tenant's `ai_actions` table -- that needs a manual
    # `ALTER TABLE ai_actions ADD COLUMN IF NOT EXISTS output_tokens INTEGER,
    # ADD COLUMN IF NOT EXISTS cached_tokens INTEGER;` against
    # `AGENT_DATABASE_URL` before any already-deployed tenant will have them.
    # See docs/analysis/2026-08-09-blocked-work-register.md.
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cached_tokens: Mapped[int | None] = mapped_column(Integer)
    output: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ConversationLifecycle(Base):
    """Per-conversation lifecycle state for the auto-close / survey flow.

    Layered on top of Chatwoot's own pending/open/resolved status. The unique
    `conversation_id` primary key is the concurrency guard: the scanner and the
    orchestrator both drive transitions, and a row can only be in one state, so
    a duplicate scan tick can't double-fire a transition. `state_changed_at`
    dates the current state (used for the confirmation/survey timeout);
    `warned_at` dates when the idle warning was posted.
    """

    __tablename__ = "conversation_lifecycle"

    conversation_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[str | None] = mapped_column(Text)
    survey_variant: Mapped[str | None] = mapped_column(Text)
    warned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
