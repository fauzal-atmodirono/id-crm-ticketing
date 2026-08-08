"""P1 task 2 — the single entry point every SLA threshold comparison goes through.

The load-bearing test here is
``test_working_hours_false_matches_the_old_age_seconds_arithmetic_exactly``:
it asserts the working_hours=False path reproduces ``_conversation_age_seconds``
to the second. That equivalence is the entire safety argument for shipping the
working-hours clock dark on a live tenant.
"""

from datetime import UTC, datetime

import pytest

from chatbot.features.chat.sla import _conversation_age_seconds
from chatbot.features.chat.sla_clock import InboxCache, elapsed_minutes

INBOX_9_TO_6 = {
    "working_hours_enabled": True,
    "timezone": "UTC",
    "working_hours": [
        {"day_of_week": d, "open_hour": 9, "open_minutes": 0, "close_hour": 18,
         "close_minutes": 0, "open_all_day": False, "closed_all_day": False}
        for d in (1, 2, 3, 4, 5)
    ] + [
        {"day_of_week": d, "closed_all_day": True} for d in (0, 6)
    ],
}

NO_HOURS = {"working_hours_enabled": False}


def test_working_hours_false_returns_plain_calendar_minutes():
    start = datetime(2026, 7, 3, 17, 0, tzinfo=UTC)
    now = datetime(2026, 7, 6, 10, 0, tzinfo=UTC)
    expected = (now - start).total_seconds() / 60
    assert elapsed_minutes(start, now, INBOX_9_TO_6, working_hours=False) == expected


def test_working_hours_false_matches_the_old_age_seconds_arithmetic_exactly():
    """The safety argument for shipping this dark. Do not delete."""
    start = datetime(2026, 7, 3, 17, 0, tzinfo=UTC)
    now = datetime(2026, 7, 6, 10, 13, 37, tzinfo=UTC)
    conv = {"created_at": start.timestamp()}

    old_seconds = _conversation_age_seconds(conv, now)
    new_seconds = elapsed_minutes(start, now, INBOX_9_TO_6, working_hours=False) * 60

    assert new_seconds == old_seconds


def test_working_hours_true_excludes_a_weekend():
    start = datetime(2026, 7, 3, 17, 0, tzinfo=UTC)  # Friday 17:00
    now = datetime(2026, 7, 6, 10, 0, tzinfo=UTC)     # Monday 10:00
    # Fri 17:00-18:00 = 60, weekend = 0, Mon 09:00-10:00 = 60
    assert elapsed_minutes(start, now, INBOX_9_TO_6, working_hours=True) == 120


def test_working_hours_true_on_an_inbox_with_no_config_equals_calendar_minutes():
    start = datetime(2026, 7, 3, 17, 0, tzinfo=UTC)
    now = datetime(2026, 7, 6, 10, 0, tzinfo=UTC)
    assert elapsed_minutes(start, now, NO_HOURS, working_hours=True) == elapsed_minutes(
        start, now, NO_HOURS, working_hours=False
    )


def test_working_hours_true_on_an_empty_inbox_dict_equals_calendar_minutes():
    """An inbox fetch failure yields {} — it must fall through to wall clock."""
    start = datetime(2026, 7, 3, 17, 0, tzinfo=UTC)
    now = datetime(2026, 7, 6, 10, 0, tzinfo=UTC)
    assert elapsed_minutes(start, now, {}, working_hours=True) == elapsed_minutes(
        start, now, {}, working_hours=False
    )


def test_end_before_start_is_zero_not_negative():
    start = datetime(2026, 7, 6, 12, 0, tzinfo=UTC)
    now = datetime(2026, 7, 6, 9, 0, tzinfo=UTC)
    assert elapsed_minutes(start, now, INBOX_9_TO_6, working_hours=True) == 0
    assert elapsed_minutes(start, now, INBOX_9_TO_6, working_hours=False) == 0


# --- InboxCache -----------------------------------------------------------


_UNSET = object()


class _FakeChatwoot:
    def __init__(self, inbox=_UNSET, raises=False):
        self._inbox = INBOX_9_TO_6 if inbox is _UNSET else inbox
        self._raises = raises
        self.calls: list[int] = []

    async def get_inbox_working_hours(self, inbox_id):
        self.calls.append(inbox_id)
        if self._raises:
            raise RuntimeError("chatwoot is down")
        return self._inbox


async def test_inbox_cache_fetches_each_inbox_once_per_instance():
    client = _FakeChatwoot()
    cache = InboxCache(client)

    assert await cache.get(7) == INBOX_9_TO_6
    assert await cache.get(7) == INBOX_9_TO_6
    assert await cache.get(7) == INBOX_9_TO_6

    assert client.calls == [7]


async def test_inbox_cache_returns_empty_dict_when_the_fetch_raises():
    cache = InboxCache(_FakeChatwoot(raises=True))
    assert await cache.get(7) == {}


async def test_a_failed_fetch_is_not_retried_for_the_same_scan():
    """Fail open, but do not amplify a Chatwoot outage into N retries."""
    client = _FakeChatwoot(raises=True)
    cache = InboxCache(client)

    await cache.get(7)
    await cache.get(7)

    assert client.calls == [7]


async def test_inbox_cache_returns_empty_dict_for_a_none_inbox_id():
    client = _FakeChatwoot()
    cache = InboxCache(client)

    assert await cache.get(None) == {}
    assert client.calls == []


async def test_inbox_cache_returns_empty_dict_when_there_is_no_client():
    cache = InboxCache(None)
    assert await cache.get(7) == {}


async def test_a_none_return_from_the_port_becomes_an_empty_dict():
    """get_inbox_working_hours returns None on any failure, by contract."""
    cache = InboxCache(_FakeChatwoot(inbox=None))
    assert await cache.get(7) == {}


async def test_two_cache_instances_do_not_share_state():
    client_a = _FakeChatwoot()
    client_b = _FakeChatwoot()

    await InboxCache(client_a).get(7)
    await InboxCache(client_b).get(7)

    assert client_a.calls == [7]
    assert client_b.calls == [7]


@pytest.mark.parametrize("working_hours", [True, False])
def test_a_naive_start_is_treated_as_utc_rather_than_raising(working_hours):
    start = datetime(2026, 7, 6, 9, 0)  # no tzinfo
    now = datetime(2026, 7, 6, 11, 0, tzinfo=UTC)
    assert elapsed_minutes(start, now, INBOX_9_TO_6, working_hours=working_hours) == 120
