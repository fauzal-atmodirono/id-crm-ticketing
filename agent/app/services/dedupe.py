"""Idempotency helper for webhook deliveries.

Both webhook routers dedupe on a provider-supplied delivery id via an
insert-or-skip against `processed_deliveries`. Relying on the primary-key
insert (rather than check-then-insert) makes the claim atomic under
concurrent delivery of the same webhook. A missing delivery id means the
source didn't provide one, so we can't dedupe and just process the request.
"""

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
