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


def _assigned_conv_client(idle_minutes: int, now: datetime):
    """A chatwoot client returning one open, ASSIGNED conversation idle
    `idle_minutes` minutes (in inbox 4, WhatsApp)."""
    client = AsyncMock()
    last_activity = int(now.timestamp()) - idle_minutes * 60
    client.list_conversations.side_effect = lambda status=None, assignee_type=None: (
        {"data": {"payload": [
            {"id": 71, "inbox_id": 4, "last_activity_at": last_activity,
             "meta": {"assignee": {"id": 1}}},
        ]}} if status == "open" else {"data": {"payload": []}}
    )
    client.get_inbox.return_value = {
        "channel_type": "Channel::Whatsapp", "working_hours_enabled": False,
    }
    return client


@pytest.fixture
def assigned_wired(monkeypatch):
    now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
    client = _assigned_conv_client(idle_minutes=40, now=now)
    monkeypatch.setattr(lifecycle_scanner, "get_chatwoot_client", lambda: client)
    monkeypatch.setattr(lifecycle, "get_chatwoot_client", lambda: client)
    monkeypatch.setattr(lifecycle_scanner, "_now", lambda: now)
    return client


async def test_scan_auto_resolves_stale_assigned_handoff(assigned_wired, monkeypatch):
    # An assigned (handed-off) conversation idle past the SLA is silently
    # auto-resolved so the abandoned handoff can't swallow future messages.
    from app.config import get_settings

    monkeypatch.setattr(
        get_settings(), "lifecycle_assigned_idle_resolve_minutes", 30, raising=False
    )
    await lifecycle_scanner.scan_once()
    assert await lifecycle_store.get_state(71) == "closed"
    assigned_wired.toggle_status.assert_awaited_with(71, "resolved")


async def test_scan_leaves_assigned_untouched_when_disabled(assigned_wired, monkeypatch):
    # Default (0) disables the feature: an assigned conversation is left entirely
    # to the human — never seeded, never resolved.
    from app.config import get_settings

    monkeypatch.setattr(
        get_settings(), "lifecycle_assigned_idle_resolve_minutes", 0, raising=False
    )
    await lifecycle_scanner.scan_once()
    assert await lifecycle_store.get_state(71) is None
    assigned_wired.toggle_status.assert_not_awaited()


async def test_scan_leaves_assigned_untouched_when_not_idle_enough(monkeypatch):
    # Assigned but only idle 5 min < 30 min SLA → left alone (human may still be
    # mid-conversation).
    from app.config import get_settings

    now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
    client = _assigned_conv_client(idle_minutes=5, now=now)
    monkeypatch.setattr(lifecycle_scanner, "get_chatwoot_client", lambda: client)
    monkeypatch.setattr(lifecycle, "get_chatwoot_client", lambda: client)
    monkeypatch.setattr(lifecycle_scanner, "_now", lambda: now)
    monkeypatch.setattr(
        get_settings(), "lifecycle_assigned_idle_resolve_minutes", 30, raising=False
    )
    await lifecycle_scanner.scan_once()
    assert await lifecycle_store.get_state(71) is None
    client.toggle_status.assert_not_awaited()


async def test_scan_uses_per_inbox_warn_override(wired, monkeypatch):
    # Env default warn=10; conversation is idle 12 min. Override warn to 20 min
    # (via per-inbox timing) so the conversation is NOT yet warned.
    from app.services import lifecycle

    async def _timing(inbox_id):
        return {"idle_warn_minutes": 20, "idle_close_grace_minutes": None,
                "idle_close_out_of_hours_grace_minutes": None, "confirm_grace_minutes": None}

    monkeypatch.setattr(lifecycle, "_fetch_lifecycle_timing", _timing)
    await lifecycle_store.seed_active(70, channel="Channel::Whatsapp")
    await lifecycle_scanner.scan_once()
    assert await lifecycle_store.get_state(70) == "active"  # 12 < 20 -> no warn
    wired.create_message.assert_not_awaited()


async def test_scan_falls_back_to_env_default_when_no_timing(wired, monkeypatch):
    # No per-inbox timing -> env default warn=10; idle 12 -> warned (today's behavior).
    from app.services import lifecycle

    async def _timing(inbox_id):
        return None

    monkeypatch.setattr(lifecycle, "_fetch_lifecycle_timing", _timing)
    await lifecycle_store.seed_active(70, channel="Channel::Whatsapp")
    await lifecycle_scanner.scan_once()
    assert await lifecycle_store.get_state(70) == "idle_warned"


async def test_warn_message_uses_per_inbox_grace(wired, monkeypatch):
    # Per-inbox close grace = 7 -> the warning must say "7 minutes".
    from app.services import lifecycle

    async def _timing(inbox_id):
        return {"idle_warn_minutes": None, "idle_close_grace_minutes": 7,
                "idle_close_out_of_hours_grace_minutes": None, "confirm_grace_minutes": None}

    monkeypatch.setattr(lifecycle, "_fetch_lifecycle_timing", _timing)
    await lifecycle_store.seed_active(70, channel="Channel::Whatsapp")
    await lifecycle_scanner.scan_once()
    # The warning is the first create_message call in the warn action.
    posted = [c.args[1] for c in wired.create_message.await_args_list]
    assert any("7 minutes" in str(m) for m in posted), posted
