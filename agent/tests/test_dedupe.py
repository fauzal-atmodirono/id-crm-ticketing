"""Tests for the webhook delivery idempotency helper.

`claim_delivery` inserts a `processed_deliveries` row for a given delivery
id; if the row already exists it returns False (skip), otherwise True
(process). A missing delivery id always returns True (can't dedupe).
"""

from app.services.dedupe import claim_delivery


async def test_first_delivery_is_claimed():
    assert await claim_delivery("delivery-1", "chatwoot") is True


async def test_duplicate_delivery_is_rejected():
    assert await claim_delivery("delivery-2", "chatwoot") is True
    assert await claim_delivery("delivery-2", "chatwoot") is False


async def test_missing_delivery_id_always_processes():
    assert await claim_delivery(None, "chatwoot") is True
    assert await claim_delivery(None, "chatwoot") is True


async def test_duplicate_delivery_id_rejected_regardless_of_source():
    # processed_deliveries.delivery_id is the primary key (not scoped per
    # source), so a repeat id from a different source is still a duplicate.
    assert await claim_delivery("cross-source-id", "chatwoot") is True
    assert await claim_delivery("cross-source-id", "zammad") is False
