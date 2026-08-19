"""`customer_updated_at` -- the stop signal for the backend's customer-update
clock.

The trap this guards against: the two messages that land immediately after a
dealer replies are the linked reply and the AI draft, and BOTH are private
notes. If either counted as "the customer was updated", the clock would clear
itself the instant it started and the whole measurement would be worthless.
"""

from __future__ import annotations

import httpx
import respx

from app.clients.deps import get_chatwoot_client
from app.config import get_settings
from app.services import sync

CHATWOOT = "http://chatwoot-rails:3000"
CONV = f"{CHATWOOT}/api/v1/accounts/1/conversations/9"

_REPLIED = {"escalation_replied_at": "2026-08-19T09:00:00+00:00"}


def _message(**kw) -> dict:
    payload = {
        "message_type": "outgoing",
        "private": False,
        "conversation": {"id": 9},
    }
    payload.update(kw)
    return payload


def _enable(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "escalation_customer_update_enabled", True)
    get_chatwoot_client.cache_clear()


@respx.mock
async def test_an_outgoing_public_reply_stamps_the_update(monkeypatch):
    _enable(monkeypatch)
    respx.get(CONV).mock(
        return_value=httpx.Response(200, json={"id": 9, "custom_attributes": _REPLIED})
    )
    set_attrs = respx.post(f"{CONV}/custom_attributes").mock(
        return_value=httpx.Response(200, json={})
    )

    await sync.maybe_stamp_customer_update(_message())

    assert set_attrs.called
    assert "customer_updated_at" in set_attrs.calls[0].request.content.decode()


@respx.mock
async def test_a_private_note_never_stamps(monkeypatch):
    """The dealer's linked reply and the AI draft are both private notes."""
    _enable(monkeypatch)
    set_attrs = respx.post(f"{CONV}/custom_attributes").mock(
        return_value=httpx.Response(200, json={})
    )

    await sync.maybe_stamp_customer_update(_message(private=True))

    assert not set_attrs.called


@respx.mock
async def test_an_incoming_message_never_stamps(monkeypatch):
    """The customer chasing us is not us updating the customer."""
    _enable(monkeypatch)
    set_attrs = respx.post(f"{CONV}/custom_attributes").mock(
        return_value=httpx.Response(200, json={})
    )

    await sync.maybe_stamp_customer_update(_message(message_type="incoming"))

    assert not set_attrs.called


@respx.mock
async def test_no_stamp_when_no_dealer_has_replied(monkeypatch):
    """No clock is running, so there is nothing to stop -- and stamping would
    silently satisfy a future escalation before it happened."""
    _enable(monkeypatch)
    respx.get(CONV).mock(
        return_value=httpx.Response(200, json={"id": 9, "custom_attributes": {}})
    )
    set_attrs = respx.post(f"{CONV}/custom_attributes").mock(
        return_value=httpx.Response(200, json={})
    )

    await sync.maybe_stamp_customer_update(_message())

    assert not set_attrs.called


@respx.mock
async def test_the_first_update_wins(monkeypatch):
    """A chattier agent must not be able to move their own deadline."""
    _enable(monkeypatch)
    respx.get(CONV).mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 9,
                "custom_attributes": {
                    **_REPLIED,
                    "customer_updated_at": "2026-08-19T10:00:00+00:00",
                },
            },
        )
    )
    set_attrs = respx.post(f"{CONV}/custom_attributes").mock(
        return_value=httpx.Response(200, json={})
    )

    await sync.maybe_stamp_customer_update(_message())

    assert not set_attrs.called


@respx.mock
async def test_the_flag_off_is_a_no_op(monkeypatch):
    monkeypatch.setattr(get_settings(), "escalation_customer_update_enabled", False)
    get_chatwoot_client.cache_clear()
    conv = respx.get(CONV).mock(return_value=httpx.Response(200, json={}))

    await sync.maybe_stamp_customer_update(_message())

    assert not conv.called


@respx.mock
async def test_a_chatwoot_failure_is_swallowed(monkeypatch):
    """Background tasks never raise: an exception here would only produce an
    unretrieved-exception log."""
    _enable(monkeypatch)
    respx.get(CONV).mock(return_value=httpx.Response(500))

    await sync.maybe_stamp_customer_update(_message())
