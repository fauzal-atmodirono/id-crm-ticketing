"""SQLAlchemy 2.0 declarative models for the agent service's own database.

These tables track the state the integration layer needs that doesn't
belong in either Chatwoot or Zammad: which contact/conversation maps to
which Zammad user/ticket, which webhook deliveries have already been
processed (idempotency), and a log of AI decisions.
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ContactLink(Base):
    """Maps a Chatwoot contact to the corresponding Zammad user."""

    __tablename__ = "contact_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chatwoot_contact_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, nullable=False
    )
    zammad_user_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, nullable=False
    )
    email: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ConversationLink(Base):
    """Maps a Chatwoot conversation to the corresponding Zammad ticket."""

    __tablename__ = "conversation_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chatwoot_conversation_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, nullable=False
    )
    zammad_ticket_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, nullable=False
    )
    last_synced_state: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


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
    output: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
