"""P1 task 5 — stamp the in-hours flag at intake.

Why intake and not report time: an operator can edit an inbox's working hours
at any point, so `received_in_business_hours` computed later answers "would this
have been in hours under TODAY's config", which is a different question from
"was it in hours when it arrived". The stamp is a fact about arrival, so it is
written once, on the first inbound message, and never overwritten.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.services import sync

INBOX_9_TO_6_MYT = {
    "working_hours_enabled": True,
    "timezone": "Asia/Kuala_Lumpur",
    "working_hours": [
        {"day_of_week": d, "open_hour": 9, "open_minutes": 0, "close_hour": 18,
         "close_minutes": 0, "open_all_day": False, "closed_all_day": False}
        for d in (1, 2, 3, 4, 5)
    ] + [
        {"day_of_week": d, "closed_all_day": True} for d in (0, 6)
    ],
}

# 2026-07-06 is a Monday. 03:00 UTC == 11:00 MYT (in hours).
IN_HOURS = datetime(2026, 7, 6, 3, 0, tzinfo=timezone.utc)
# 2026-07-03 is a Friday. 14:00 UTC == 22:00 MYT (after the 18:00 close).
OUT_OF_HOURS = datetime(2026, 7, 3, 14, 0, tzinfo=timezone.utc)


def _payload(**over):
    base = {
        "id": 42,
        "message_type": "incoming",
        "private": False,
        "conversation": {"id": 42, "inbox_id": 7},
        "created_at": int(IN_HOURS.timestamp()),
    }
    base.update(over)
    return base


class _Chatwoot:
    def __init__(self, existing=None, inbox=INBOX_9_TO_6_MYT, raises=False):
        self._existing = existing or {}
        self._inbox = inbox
        self._raises = raises
        self.written: dict = {}

    async def get_conversation(self, conv_id):  # noqa: ARG002
        if self._raises:
            raise RuntimeError("chatwoot down")
        return {"custom_attributes": self._existing, "inbox_id": 7}

    async def get_inbox(self, inbox_id):  # noqa: ARG002
        return self._inbox

    async def set_custom_attributes(self, conv_id, attrs):  # noqa: ARG002
        if self._raises:
            raise RuntimeError("chatwoot down")
        self.written.update(attrs)


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("BUSINESS_HOURS_STAMP_ENABLED", "true")
    sync.get_settings.cache_clear()
    yield
    sync.get_settings.cache_clear()


@pytest.fixture
def disabled(monkeypatch):
    monkeypatch.delenv("BUSINESS_HOURS_STAMP_ENABLED", raising=False)
    sync.get_settings.cache_clear()
    yield
    sync.get_settings.cache_clear()


async def _run(payload, client):
    with patch.object(sync, "get_chatwoot_client", return_value=client):
        await sync.maybe_stamp_business_hours(payload)


async def test_the_first_inbound_message_stamps_the_flag(enabled):
    client = _Chatwoot()
    await _run(_payload(), client)
    assert "received_in_business_hours" in client.written


async def test_an_in_hours_arrival_stamps_true_and_no_attend_after(enabled):
    client = _Chatwoot()
    await _run(_payload(created_at=int(IN_HOURS.timestamp())), client)

    assert client.written["received_in_business_hours"] is True
    assert "attend_after" not in client.written


async def test_an_out_of_hours_arrival_stamps_false_and_an_attend_after(enabled):
    client = _Chatwoot()
    await _run(_payload(created_at=int(OUT_OF_HOURS.timestamp())), client)

    assert client.written["received_in_business_hours"] is False
    assert client.written["attend_after"]


async def test_the_attend_after_is_the_next_working_instant(enabled):
    client = _Chatwoot()
    await _run(_payload(created_at=int(OUT_OF_HOURS.timestamp())), client)

    # Friday 22:00 MYT -> Monday 09:00 MYT == 01:00 UTC Monday.
    assert client.written["attend_after"].startswith("2026-07-06T09:00")


async def test_a_second_message_never_overwrites_the_stamp(enabled):
    client = _Chatwoot(existing={"received_in_business_hours": True})
    await _run(_payload(created_at=int(OUT_OF_HOURS.timestamp())), client)
    assert client.written == {}


async def test_a_stamp_of_false_still_counts_as_already_stamped(enabled):
    """`False` is a real answer — `in existing` must be the test, not truthiness."""
    client = _Chatwoot(existing={"received_in_business_hours": False})
    await _run(_payload(), client)
    assert client.written == {}


async def test_an_outgoing_agent_message_does_not_stamp(enabled):
    client = _Chatwoot()
    await _run(_payload(message_type="outgoing"), client)
    assert client.written == {}


async def test_a_private_note_does_not_stamp(enabled):
    client = _Chatwoot()
    await _run(_payload(private=True), client)
    assert client.written == {}


async def test_a_chatwoot_api_error_is_logged_and_swallowed(enabled):
    client = _Chatwoot(raises=True)
    await _run(_payload(), client)  # must not raise
    assert client.written == {}


async def test_the_flag_off_writes_nothing(disabled):
    client = _Chatwoot()
    await _run(_payload(), client)
    assert client.written == {}


async def test_the_flag_off_makes_no_chatwoot_call_at_all(disabled):
    client = AsyncMock()
    with patch.object(sync, "get_chatwoot_client", return_value=client) as get_client:
        await sync.maybe_stamp_business_hours(_payload())
    get_client.assert_not_called()


async def test_received_at_local_is_in_the_inbox_timezone_not_utc(enabled):
    client = _Chatwoot()
    await _run(_payload(created_at=int(IN_HOURS.timestamp())), client)

    # 03:00 UTC is 11:00 in Asia/Kuala_Lumpur.
    assert client.written["received_at_local"].startswith("2026-07-06T11:00")


async def test_an_inbox_with_no_working_hours_stamps_true(enabled):
    """No configured hours means always open — same fallback as the clock."""
    client = _Chatwoot(inbox={"working_hours_enabled": False})
    await _run(_payload(created_at=int(OUT_OF_HOURS.timestamp())), client)

    assert client.written["received_in_business_hours"] is True
    assert "attend_after" not in client.written


async def test_a_missing_conversation_id_is_skipped_without_raising(enabled):
    client = _Chatwoot()
    await _run(_payload(conversation={}), client)
    assert client.written == {}
