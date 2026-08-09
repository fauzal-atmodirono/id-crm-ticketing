"""P4 task 6 — count reopens, so v_reopen_rate measures something.

`reopen_count` has been a warehouse column and a `v_reopen_rate` view since
Phase 3. Nothing ever wrote it: the Chatwoot mapper reads it from
`additional_attributes`, and no code put it there. So the reopen rate has been
a chart of zeroes that renders perfectly.

A reopen is a **resolved -> not-resolved** transition. Nothing else counts:
open -> pending is an agent moving a live case around, and open -> resolved is
the case being closed. Getting that wrong inflates a quality metric the client
reads.

Double counting matters more than under-counting here. A reopen rate that
drifts upward because a webhook was delivered twice looks exactly like a
service getting worse.
"""

import json

import httpx
import respx

from app.config import get_settings
from app.services import sync

CHATWOOT = "http://chatwoot-rails:3000"


def _payload(*, status="open", previous="resolved", conv_id=42, attrs=None):
    return {
        "event": "conversation_status_changed",
        "id": conv_id,
        "status": status,
        # Chatwoot sends the prior status under changed_attributes.
        "changed_attributes": [{"status": {"previous_value": previous, "current_value": status}}],
        "custom_attributes": attrs or {},
    }


def _enable(monkeypatch, value=True):
    monkeypatch.setattr(get_settings(), "reopen_tracking_enabled", value)


def _stub(attrs=None):
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/42").mock(
        return_value=httpx.Response(
            200, json={"id": 42, "custom_attributes": attrs or {}}
        )
    )
    return respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/42/custom_attributes"
    ).mock(return_value=httpx.Response(200, json={}))


def _written(route):
    return json.loads(route.calls.last.request.read())["custom_attributes"]


@respx.mock
async def test_a_resolved_to_open_transition_increments_reopen_count(monkeypatch):
    _enable(monkeypatch)
    route = _stub()

    await sync.record_conversation_status(_payload(status="open", previous="resolved"))

    assert _written(route)["reopen_count"] == 1
    assert "last_reopened_at" in _written(route)


@respx.mock
async def test_a_resolved_to_pending_transition_increments_reopen_count(monkeypatch):
    _enable(monkeypatch)
    route = _stub()

    await sync.record_conversation_status(_payload(status="pending", previous="resolved"))

    assert _written(route)["reopen_count"] == 1


@respx.mock
async def test_an_open_to_pending_transition_does_not_increment(monkeypatch):
    """An agent moving a live case around is not a reopen."""
    _enable(monkeypatch)
    route = _stub()

    await sync.record_conversation_status(_payload(status="pending", previous="open"))

    assert not route.called


@respx.mock
async def test_an_open_to_resolved_transition_does_not_increment(monkeypatch):
    _enable(monkeypatch)
    route = _stub()

    await sync.record_conversation_status(_payload(status="resolved", previous="open"))

    assert not route.called


@respx.mock
async def test_a_second_reopen_increments_to_two(monkeypatch):
    _enable(monkeypatch)
    route = _stub(attrs={"reopen_count": 1})

    await sync.record_conversation_status(_payload())

    assert _written(route)["reopen_count"] == 2


@respx.mock
async def test_a_non_numeric_stored_count_is_treated_as_zero(monkeypatch):
    """Chatwoot round-trips custom attributes as strings often enough that
    this is a real shape, and int('') would abort the whole task."""
    _enable(monkeypatch)
    route = _stub(attrs={"reopen_count": "not a number"})

    await sync.record_conversation_status(_payload())

    assert _written(route)["reopen_count"] == 1


@respx.mock
async def test_a_string_count_from_chatwoot_still_increments(monkeypatch):
    _enable(monkeypatch)
    route = _stub(attrs={"reopen_count": "3"})

    await sync.record_conversation_status(_payload())

    assert _written(route)["reopen_count"] == 4


@respx.mock
async def test_a_chatwoot_api_failure_is_logged_and_swallowed(monkeypatch):
    """Background tasks never raise for an expected failure."""
    _enable(monkeypatch)
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/42").mock(
        return_value=httpx.Response(500)
    )

    await sync.record_conversation_status(_payload())


@respx.mock
async def test_the_flag_off_leaves_the_stub_a_no_op(monkeypatch):
    _enable(monkeypatch, value=False)
    route = _stub()

    await sync.record_conversation_status(_payload())

    assert not route.called
    assert not respx.calls


@respx.mock
async def test_a_payload_with_no_transition_information_is_skipped(monkeypatch):
    """Without a previous status we cannot tell a reopen from a close, and
    guessing would inflate the metric."""
    _enable(monkeypatch)
    route = _stub()
    payload = _payload()
    del payload["changed_attributes"]

    await sync.record_conversation_status(payload)

    assert not route.called
