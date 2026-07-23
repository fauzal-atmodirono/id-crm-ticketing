"""DB access for conversation lifecycle state.

Thin CRUD over the `conversation_lifecycle` table. Each function owns its own
session (callers are background tasks / scanner ticks with no request-scoped
session). Uses select-then-insert/update rather than a DB-specific upsert so the
same code runs on sqlite (tests) and postgres (prod); the unique
`conversation_id` PK is the belt-and-suspenders guard against a racing double
insert.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.db.models import ConversationLifecycle
from app.db.session import async_session_maker


async def get_row(conversation_id: int) -> ConversationLifecycle | None:
    async with async_session_maker() as session:
        result = await session.execute(
            select(ConversationLifecycle).where(
                ConversationLifecycle.conversation_id == conversation_id
            )
        )
        return result.scalar_one_or_none()


async def get_state(conversation_id: int) -> str | None:
    row = await get_row(conversation_id)
    return row.state if row is not None else None


async def seed_active(conversation_id: int, channel: str | None) -> None:
    """Insert an ACTIVE row if none exists. No-op when a row already exists —
    never clobbers a conversation already mid-lifecycle."""
    async with async_session_maker() as session:
        result = await session.execute(
            select(ConversationLifecycle).where(
                ConversationLifecycle.conversation_id == conversation_id
            )
        )
        if result.scalar_one_or_none() is not None:
            return
        session.add(
            ConversationLifecycle(
                conversation_id=conversation_id, state="active", channel=channel
            )
        )
        await session.commit()


async def transition(
    conversation_id: int,
    new_state: str,
    *,
    warned_at: datetime | None = None,
    survey_variant: str | None = None,
) -> None:
    """Set the row's state (creating the row if absent), stamping
    state_changed_at. Optional warned_at / survey_variant are set only when
    provided (None leaves the existing value untouched)."""
    now = datetime.now(timezone.utc)
    async with async_session_maker() as session:
        result = await session.execute(
            select(ConversationLifecycle).where(
                ConversationLifecycle.conversation_id == conversation_id
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            row = ConversationLifecycle(conversation_id=conversation_id, state=new_state)
            session.add(row)
        else:
            row.state = new_state
        row.state_changed_at = now
        if warned_at is not None:
            row.warned_at = warned_at
        if survey_variant is not None:
            row.survey_variant = survey_variant
        await session.commit()
