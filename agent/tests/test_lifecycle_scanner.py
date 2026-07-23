from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from app.services import lifecycle, lifecycle_scanner, lifecycle_store


def test_decide_idle_action_transitions():
    d = lifecycle_scanner.decide_idle_action
    assert d("active", 12, 0, 10, 15, 10) == "warn"
    assert d("active", 3, 0, 10, 15, 10) is None
    assert d("idle_warned", 16, 6, 10, 15, 10) == "close"
    assert d("idle_warned", 12, 2, 10, 15, 10) is None
    assert d("awaiting_resolution", 0, 11, 10, 15, 10) == "resolve_timeout"
    assert d("awaiting_survey", 0, 11, 10, 15, 10) == "resolve_timeout"
    assert d("closed", 999, 999, 10, 15, 10) is None


@pytest.fixture
def wired(monkeypatch):
    client = AsyncMock()
    # One WhatsApp conversation, no assignee, idle 12 minutes.
    now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
    last_activity = int((now.timestamp())) - 12 * 60
    client.list_conversations.side_effect = lambda status=None, assignee_type=None: (
        {"data": {"payload": [
            {"id": 70, "inbox_id": 4, "last_activity_at": last_activity,
             "meta": {"assignee": None}},
        ]}} if status == "pending" else {"data": {"payload": []}}
    )
    client.get_inbox.return_value = {
        "channel_type": "Channel::Whatsapp", "working_hours_enabled": False,
    }
    monkeypatch.setattr(lifecycle_scanner, "get_chatwoot_client", lambda: client)
    monkeypatch.setattr(lifecycle, "get_chatwoot_client", lambda: client)
    monkeypatch.setattr(lifecycle_scanner, "_now", lambda: now)
    return client


async def test_scan_warns_idle_conversation(wired):
    await lifecycle_store.seed_active(70, channel="Channel::Whatsapp")
    await lifecycle_scanner.scan_once()
    assert await lifecycle_store.get_state(70) == "idle_warned"
    wired.create_message.assert_awaited()  # warning posted


async def test_scan_skips_email_channel(wired, monkeypatch):
    wired.get_inbox.return_value = {
        "channel_type": "Channel::Email", "working_hours_enabled": False,
    }
    await lifecycle_store.seed_active(70, channel="Channel::Email")
    await lifecycle_scanner.scan_once()
    assert await lifecycle_store.get_state(70) == "active"  # untouched
