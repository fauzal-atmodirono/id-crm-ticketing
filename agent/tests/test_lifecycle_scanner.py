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


async def test_scan_skips_disabled_inbox(wired, monkeypatch):
    from app.services import lifecycle

    async def _timing(inbox_id):
        return {"inactivity_enabled": False}

    monkeypatch.setattr(lifecycle, "_fetch_lifecycle_timing", _timing)
    await lifecycle_store.seed_active(70, channel="Channel::Whatsapp")
    await lifecycle_scanner.scan_once()
    assert await lifecycle_store.get_state(70) == "active"  # disabled -> no warn
    wired.create_message.assert_not_awaited()


async def test_scan_uses_per_inbox_warning_message(wired, monkeypatch):
    from app.services import lifecycle

    async def _timing(inbox_id):
        return {"idle_close_grace_minutes": 4,
                "idle_warning_message": "Auto-close in {{minutes}}m."}

    monkeypatch.setattr(lifecycle, "_fetch_lifecycle_timing", _timing)
    await lifecycle_store.seed_active(70, channel="Channel::Whatsapp")
    await lifecycle_scanner.scan_once()
    posted = [c.args[1] for c in wired.create_message.await_args_list]
    assert any("Auto-close in 4m." in str(m) for m in posted), posted


async def test_close_uses_per_inbox_messages(wired, monkeypatch):
    from datetime import datetime, timezone
    from app.services import lifecycle, lifecycle_store

    async def _timing(inbox_id):
        # grace=2 -> close_after=10+2=12 <= idle=12, so "close" fires.
        return {
            "idle_close_grace_minutes": 2,
            "idle_close_message": "BYE",
            "resolution_prompt_message": "OK? Y/N",
        }

    monkeypatch.setattr(lifecycle, "_fetch_lifecycle_timing", _timing)
    # Seed IDLE_WARNED so decide_idle_action evaluates the close branch.
    await lifecycle_store.seed_active(70, channel="Channel::Whatsapp")
    await lifecycle_store.transition(
        70, lifecycle.IDLE_WARNED, warned_at=datetime(2026, 7, 20, 11, 40, tzinfo=timezone.utc)
    )
    await lifecycle_scanner.scan_once()
    posted = [c.args[1] for c in wired.create_message.await_args_list]
    assert any("BYE" in str(m) for m in posted), posted
    assert any("OK? Y/N" in str(m) for m in posted), posted


async def test_scan_leaves_bot_owned_pending_conversations_alone(wired, monkeypatch):
    """`lifecycle_skip_pending` keeps us off conversations a bot still owns.

    On a tenant whose inbox is driven by a third-party agent bot (aeon360),
    `pending` means *that* bot is mid-conversation. Our idle warning posts as
    an outgoing message from a user, which its decision table reads as a human
    taking over: it cancels the in-flight generation and flips the
    conversation to `open`. Observed live on 2026-08-21 — every one of our
    lifecycle posts matched one of their `toggle_status` calls to the second.
    """
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "lifecycle_skip_pending", True, raising=False)
    await lifecycle_store.seed_active(70, channel="Channel::Whatsapp")

    await lifecycle_scanner.scan_once()

    # Never even listed: the conversation is not ours to sweep.
    for call in wired.list_conversations.await_args_list:
        assert call.kwargs.get("status") != "pending"
    assert await lifecycle_store.get_state(70) == "active"
    wired.create_message.assert_not_awaited()


async def test_scan_still_sweeps_pending_by_default(wired):
    """Default is off, so every existing tenant behaves exactly as before."""
    from app.config import get_settings

    assert get_settings().lifecycle_skip_pending is False
    await lifecycle_store.seed_active(70, channel="Channel::Whatsapp")

    await lifecycle_scanner.scan_once()

    assert await lifecycle_store.get_state(70) == "idle_warned"
    wired.create_message.assert_awaited()


def test_decide_idle_action_closes_grace_after_the_warning_not_after_the_idle_reset():
    """FIX-52: the warning is itself a message, so Chatwoot's last_activity_at
    resets when it is posted and the old `idle >= warn+grace` check needed
    warn+grace MORE minutes (~16 live instead of 5). The close is measured from
    the warning (state age) against the grace the customer was promised."""
    d = lifecycle_scanner.decide_idle_action
    # warned 5.2 min ago, idle clock reset by the warning to 5.2 min (< 15)
    assert d("idle_warned", 5.2, 5.2, 10, 15, 10, grace_after=5) == "close"
    assert d("idle_warned", 4.0, 4.0, 10, 15, 10, grace_after=5) is None
    # no grace given -> legacy behaviour
    assert d("idle_warned", 5.2, 5.2, 10, 15, 10) is None


async def _warned_conv_wired(monkeypatch, minutes_since_warning: float):
    from datetime import timedelta

    client = AsyncMock()
    base = datetime.now(timezone.utc)
    now = base + timedelta(minutes=minutes_since_warning)
    # last_activity_at == the warning message, so idle == minutes_since_warning
    client.list_conversations.side_effect = lambda status=None, assignee_type=None: (
        {"data": {"payload": [
            {"id": 71, "inbox_id": 4, "last_activity_at": int(base.timestamp()),
             "meta": {"assignee": None}},
        ]}} if status == "pending" else {"data": {"payload": []}}
    )
    client.get_inbox.return_value = {
        "channel_type": "Channel::Whatsapp", "working_hours_enabled": False,
    }
    monkeypatch.setattr(lifecycle_scanner, "get_chatwoot_client", lambda: client)
    monkeypatch.setattr(lifecycle, "get_chatwoot_client", lambda: client)
    monkeypatch.setattr(lifecycle_scanner, "_now", lambda: now)
    await lifecycle_store.seed_active(71, channel="Channel::Whatsapp")
    await lifecycle_store.transition(71, lifecycle.IDLE_WARNED, warned_at=base)
    return client


async def test_scan_closes_within_one_sweep_of_warn_plus_grace(monkeypatch):
    # default grace is 5 min; 5.5 min after the warning (one 60 s sweep later)
    client = await _warned_conv_wired(monkeypatch, 5.5)
    await lifecycle_scanner.scan_once()
    assert await lifecycle_store.get_state(71) == "awaiting_resolution"
    assert client.create_message.await_count == 2  # close text + resolution prompt


async def test_scan_does_not_close_before_grace_elapses(monkeypatch):
    client = await _warned_conv_wired(monkeypatch, 3.0)
    await lifecycle_scanner.scan_once()
    assert await lifecycle_store.get_state(71) == "idle_warned"
    client.create_message.assert_not_awaited()


def test_decide_idle_action_does_not_close_a_customer_who_replied():
    d = lifecycle_scanner.decide_idle_action
    assert d("idle_warned", 1, 6, 10, 15, 10, grace_after=5, replied_since_warning=True) is None


async def test_scan_does_not_close_when_customer_replied_after_warning(monkeypatch):
    """FIX-52 review: the state was NOT reset to ACTIVE (bot webhook missed) but
    last_activity_at moved past warned_at -> a reply, so never close."""
    from datetime import timedelta

    client = AsyncMock()
    base = datetime.now(timezone.utc)
    now = base + timedelta(minutes=6)
    reply_at = base + timedelta(minutes=3)
    client.list_conversations.side_effect = lambda status=None, assignee_type=None: (
        {"data": {"payload": [
            {"id": 72, "inbox_id": 4, "last_activity_at": int(reply_at.timestamp()),
             "meta": {"assignee": None}},
        ]}} if status == "pending" else {"data": {"payload": []}}
    )
    client.get_inbox.return_value = {
        "channel_type": "Channel::Whatsapp", "working_hours_enabled": False,
    }
    monkeypatch.setattr(lifecycle_scanner, "get_chatwoot_client", lambda: client)
    monkeypatch.setattr(lifecycle, "get_chatwoot_client", lambda: client)
    monkeypatch.setattr(lifecycle_scanner, "_now", lambda: now)
    await lifecycle_store.seed_active(72, channel="Channel::Whatsapp")
    await lifecycle_store.transition(72, lifecycle.IDLE_WARNED, warned_at=base)
    await lifecycle_scanner.scan_once()
    assert await lifecycle_store.get_state(72) == "idle_warned"
    client.create_message.assert_not_awaited()


def test_idle_warning_with_zero_grace_does_not_say_zero_minutes():
    text = lifecycle.render_idle_warning(lifecycle.IDLE_WARNING_DEFAULT, 0)
    assert "0 minutes" not in text and "shortly" in text
    assert lifecycle.render_idle_warning("Closing in {{minutes}} minutes.", 5) == "Closing in 5 minutes."


class _Row:
    def __init__(self, warned_at):
        self.warned_at = warned_at
        self.state_changed_at = warned_at


def test_replied_since_warning_tolerance_boundaries():
    warned = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
    ts = warned.timestamp()
    f = lifecycle_scanner._replied_since_warning
    assert f(_Row(warned), {"last_activity_at": ts}) is False  # the warning's own bump
    assert f(_Row(warned), {"last_activity_at": ts + 29}) is False
    assert f(_Row(warned), {"last_activity_at": ts + 31}) is True  # a real reply


async def test_slow_sweep_warning_post_is_not_mistaken_for_a_reply(monkeypatch):
    """FIX-52 hardening: the warning post lands 31 s after the sweep started
    (slow sweep). warned_at must be stamped after the post, so the post's own
    last_activity_at bump is not read as a customer reply and the grace close
    still fires."""
    from datetime import timedelta

    client = AsyncMock()
    t0 = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
    post_time = t0 + timedelta(seconds=31)
    activity = {"ts": int((t0 - timedelta(minutes=12)).timestamp())}
    client.list_conversations.side_effect = lambda status=None, assignee_type=None: (
        {"data": {"payload": [
            {"id": 73, "inbox_id": 4, "last_activity_at": activity["ts"],
             "meta": {"assignee": None}},
        ]}} if status == "pending" else {"data": {"payload": []}}
    )
    client.get_inbox.return_value = {
        "channel_type": "Channel::Whatsapp", "working_hours_enabled": False,
    }
    monkeypatch.setattr(lifecycle_scanner, "get_chatwoot_client", lambda: client)
    monkeypatch.setattr(lifecycle, "get_chatwoot_client", lambda: client)

    clock = iter([t0, post_time])  # sweep start, then right after the post
    monkeypatch.setattr(lifecycle_scanner, "_now", lambda: next(clock))
    await lifecycle_scanner.scan_once()
    assert await lifecycle_store.get_state(73) == "idle_warned"
    row = await lifecycle_store.get_row(73)
    warned = row.warned_at if row.warned_at.tzinfo else row.warned_at.replace(tzinfo=timezone.utc)
    assert warned == post_time

    # The post bumped last_activity_at to post_time; next sweep is past grace.
    activity["ts"] = int(post_time.timestamp())
    # state age uses the real clock stamp from transition(); make it old enough
    later = datetime.now(timezone.utc) + timedelta(minutes=6)
    monkeypatch.setattr(lifecycle_scanner, "_now", lambda: later)
    await lifecycle_scanner.scan_once()
    assert await lifecycle_store.get_state(73) == "awaiting_resolution"


async def test_outgoing_agent_message_after_warning_blocks_the_grace_close(monkeypatch):
    """Intended: last_activity_at moves on ANY message, so an agent/staff note
    sent after the warning means someone engaged and the idle close must not
    fire on the customer's behalf."""
    from datetime import timedelta

    client = AsyncMock()
    base = datetime.now(timezone.utc)
    agent_msg_at = base + timedelta(minutes=2)
    client.list_conversations.side_effect = lambda status=None, assignee_type=None: (
        {"data": {"payload": [
            {"id": 74, "inbox_id": 4, "last_activity_at": int(agent_msg_at.timestamp()),
             "meta": {"assignee": None}},
        ]}} if status == "pending" else {"data": {"payload": []}}
    )
    client.get_inbox.return_value = {
        "channel_type": "Channel::Whatsapp", "working_hours_enabled": False,
    }
    monkeypatch.setattr(lifecycle_scanner, "get_chatwoot_client", lambda: client)
    monkeypatch.setattr(lifecycle, "get_chatwoot_client", lambda: client)
    monkeypatch.setattr(lifecycle_scanner, "_now", lambda: base + timedelta(minutes=6))
    await lifecycle_store.seed_active(74, channel="Channel::Whatsapp")
    await lifecycle_store.transition(74, lifecycle.IDLE_WARNED, warned_at=base)
    await lifecycle_scanner.scan_once()
    assert await lifecycle_store.get_state(74) == "idle_warned"
    client.create_message.assert_not_awaited()
