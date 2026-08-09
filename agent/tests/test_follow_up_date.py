"""P6 task 10 — an agent-settable follow-up REMINDER DATE, distinct from the
SLA deadline machinery.

Chatwoot writes whatever the CRM panel gives it straight into
`custom_attributes.follow_up_at` -- there is no validation on their end -- so
`maybe_validate_follow_up_date` is the write boundary: an unparseable string
or a date already in the past is corrected back to cleared (never left
standing) and the agent is told why via a private note, the one channel
guaranteed visible on the case they are looking at right now.

The separation guarantee (a follow-up date is an agent's own reminder, an
SLA deadline is a policy commitment, and the two must never merge) is proven
on the backend side, where the actual merge risk lives -- see
`backend/apps/backend/src/chatbot/features/tasks/test_deadline.py`'s
`test_a_follow_up_date_is_not_treated_as_an_sla_deadline` and
`test_sla_minutes_behaviour_is_completely_unchanged`. This module only
covers the write-boundary half of the seven-test brief: setting a valid
date passes through untouched, and a past date is rejected with a usable
message.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.services import sync


def _payload(**over):
    base = {
        "id": 42,
        "event": "conversation_updated",
        "custom_attributes": {},
    }
    base.update(over)
    return base


class _Chatwoot:
    def __init__(self, raises: bool = False):
        self._raises = raises
        self.written: dict = {}
        self.messages: list[dict] = []

    async def set_custom_attributes(self, conv_id, attrs):  # noqa: ARG002
        if self._raises:
            raise RuntimeError("chatwoot down")
        self.written.update(attrs)

    async def create_message(self, conv_id, content, private=True, **kw):  # noqa: ARG002
        if self._raises:
            raise RuntimeError("chatwoot down")
        self.messages.append({"content": content, "private": private})


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("FOLLOW_UP_DATE_ENABLED", "true")
    sync.get_settings.cache_clear()
    yield
    sync.get_settings.cache_clear()


@pytest.fixture
def disabled(monkeypatch):
    monkeypatch.delenv("FOLLOW_UP_DATE_ENABLED", raising=False)
    sync.get_settings.cache_clear()
    yield
    sync.get_settings.cache_clear()


async def _run(payload, client):
    with patch.object(sync, "get_chatwoot_client", return_value=client):
        await sync.maybe_validate_follow_up_date(payload)


async def test_a_follow_up_date_can_be_set_on_a_conversation(enabled):
    future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    client = _Chatwoot()

    await _run(_payload(custom_attributes={"follow_up_at": future}), client)

    # A valid future date is left exactly as the agent set it: no correction
    # written back, no note posted.
    assert client.written == {}
    assert client.messages == []


async def test_a_past_date_is_rejected_with_a_usable_message(enabled):
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    client = _Chatwoot()

    await _run(_payload(custom_attributes={"follow_up_at": past}), client)

    # Rejected at the write boundary: cleared, never left standing as a
    # reminder that will silently never fire.
    assert client.written == {"follow_up_at": None}
    assert len(client.messages) == 1
    message = client.messages[0]["content"]
    assert client.messages[0]["private"] is True
    # "Usable" per the brief: says what was wrong and what's acceptable, not
    # a bare "invalid date".
    assert message not in ("invalid date", "Invalid date")
    assert "past" in message.lower()
    assert "future" in message.lower() or "after" in message.lower()


async def test_an_unparseable_date_is_rejected_with_a_distinct_message(enabled):
    """An unparseable string and a past date are different operator mistakes
    and must not share a message: one is a format problem, the other a
    timing problem."""
    client = _Chatwoot()

    await _run(_payload(custom_attributes={"follow_up_at": "next thursday"}), client)

    assert client.written == {"follow_up_at": None}
    message = client.messages[0]["content"]
    assert "understood" in message.lower() or "iso" in message.lower()
    assert "past" not in message.lower()


async def test_clearing_the_date_is_accepted_without_correction(enabled):
    """Liberal in what "cleared" means on the wire: an empty string and an
    explicit null must both be accepted as a deliberate clear, not treated
    as an unparseable date -- the CRM's date-picker clear action may send
    either depending on how it's wired."""
    empty = _Chatwoot()
    await _run(_payload(custom_attributes={"follow_up_at": ""}), empty)
    assert empty.written == {}
    assert empty.messages == []

    null = _Chatwoot()
    await _run(_payload(custom_attributes={"follow_up_at": None}), null)
    assert null.written == {}
    assert null.messages == []


async def test_sla_minutes_is_never_touched_by_the_write_boundary(enabled):
    """The write boundary only ever reads/writes `follow_up_at`. A payload
    carrying an unrelated `sla_minutes` custom attribute must come through
    with that attribute completely untouched -- proving the two never cross
    on the agent side either."""
    client = _Chatwoot()

    await _run(
        _payload(custom_attributes={"sla_minutes": 30, "follow_up_at": ""}),
        client,
    )

    assert client.written == {}
    assert client.messages == []


async def test_the_flag_off_makes_no_chatwoot_call_at_all(disabled):
    client = AsyncMock()
    with patch.object(sync, "get_chatwoot_client", return_value=client) as get_client:
        await sync.maybe_validate_follow_up_date(
            _payload(custom_attributes={"follow_up_at": "not a date"})
        )
    get_client.assert_not_called()


async def test_an_untouched_attribute_is_skipped(enabled):
    """No `follow_up_at` key in this event's custom_attributes at all means
    the field wasn't part of this update -- nothing to validate."""
    client = _Chatwoot()
    await _run(_payload(custom_attributes={"case_category": "Sales"}), client)
    assert client.written == {}
    assert client.messages == []


async def test_a_missing_conversation_id_is_skipped_without_raising(enabled):
    client = _Chatwoot()
    await _run(
        _payload(id=None, custom_attributes={"follow_up_at": "garbage"}), client
    )
    assert client.written == {}
    assert client.messages == []


async def test_a_chatwoot_api_error_is_logged_and_swallowed(enabled):
    client = _Chatwoot(raises=True)
    await _run(
        _payload(custom_attributes={"follow_up_at": "garbage"}), client
    )  # must not raise
