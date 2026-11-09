"""Idempotency helper for webhook deliveries.

Both webhook routers dedupe on a provider-supplied delivery id via an
insert-or-skip against `processed_deliveries`. Relying on the primary-key
insert (rather than check-then-insert) makes the claim atomic under
concurrent delivery of the same webhook. A missing delivery id means the
source didn't provide one, so we can't dedupe and just process the request.
"""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.db.models import ProcessedDelivery
from app.db.session import async_session_maker


async def claim_delivery(delivery_id: str | None, source: str) -> bool:
    """Returns True if this delivery is new and should be processed, False
    if it has already been claimed (i.e. this is a duplicate delivery)."""
    if not delivery_id:
        return True

    async with async_session_maker() as session:
        session.add(ProcessedDelivery(delivery_id=delivery_id, source=source))
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            return False
    return True


async def release_claim(delivery_id: str) -> None:
    """Delete a claim so the work can be retried later. For callers that
    claim a logical key (not a webhook delivery) before a write that then
    failed: keeping the claim would make the failure permanent."""
    async with async_session_maker() as session:
        row = await session.get(ProcessedDelivery, delivery_id)
        if row is not None:
            await session.delete(row)
            await session.commit()


async def count_attempts(prefix: str, window_seconds: float) -> int:
    """How many attempt rows recorded under `prefix` fall inside the window.

    Attempts reuse `processed_deliveries` (one row per attempt, keyed
    `<prefix>:<uuid>`) rather than a new table: the same store, no schema
    change. `received_at` is written from Python in `record_attempt`, so the
    comparison here is against a value of the same form on sqlite and postgres.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
    async with async_session_maker() as session:
        result = await session.execute(
            select(func.count())
            .select_from(ProcessedDelivery)
            .where(ProcessedDelivery.delivery_id.like(f"{prefix}:%"))
            .where(ProcessedDelivery.received_at >= cutoff)
        )
        return int(result.scalar_one())


async def record_attempt(prefix: str, source: str, window_seconds: float) -> int:
    """Record one attempt under `prefix`; return the count inside the window,
    including this one."""
    async with async_session_maker() as session:
        session.add(
            ProcessedDelivery(
                delivery_id=f"{prefix}:{uuid.uuid4().hex}",
                source=source,
                received_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()
    return await count_attempts(prefix, window_seconds)
