"""P2 task 2 — the `escalate` label must notify somebody on every channel.

Before this, `_maybe_notify_email_escalation` returned early unless the inbox
was `Channel::Email`. Labelling a WhatsApp, Web or Phone case `escalate`
therefore escalated NOTHING, silently -- the operator saw the label stick and
assumed it had worked. That is the defect this package exists to fix.

The first two tests are the ship-dark guarantee: with
`escalation_all_channels_enabled` off, every channel behaves exactly as it does
today. Keep them.
"""

import json

import httpx
import respx

from app.clients.deps import get_proton_config_client
from app.config import get_settings
from app.services import sync

CHATWOOT = "http://chatwoot-rails:3000"
PROTON = "http://proton-backend:8080"


def _payload(*, labels=("escalate",), conv_id=42, attrs=None):
    return {
        "event": "conversation_updated",
        "id": conv_id,
        "labels": list(labels),
        "custom_attributes": attrs or {},
    }


def _enable(monkeypatch, *, all_channels: bool):
    monkeypatch.setattr(get_settings(), "email_escalation_enabled", True)
    monkeypatch.setattr(
        get_settings(), "escalation_all_channels_enabled", all_channels
    )
    monkeypatch.setattr(get_settings(), "proton_backend_url", PROTON)
    monkeypatch.setattr(get_settings(), "proton_backend_key", "k")
    get_proton_config_client.cache_clear()


def _stub(*, channel_type, conv_attrs=None, inbox_raises=False):
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/42").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 42,
                "inbox_id": 4,
                "custom_attributes": conv_attrs or {},
                "meta": {"sender": {"email": "customer@test"}},
            },
        )
    )
    if inbox_raises:
        respx.get(f"{CHATWOOT}/api/v1/accounts/1/inboxes/4").mock(
            return_value=httpx.Response(500)
        )
    else:
        respx.get(f"{CHATWOOT}/api/v1/accounts/1/inboxes/4").mock(
            return_value=httpx.Response(
                200, json={"id": 4, "channel_type": channel_type}
            )
        )
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages").mock(
        return_value=httpx.Response(
            200,
            json={"payload": [
                {"message_type": 0, "content": "my car will not start",
                 "sender": {"name": "Customer"}, "private": False}
            ]},
        )
    )
    respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/custom_attributes").mock(
        return_value=httpx.Response(200, json={})
    )
    return respx.post(f"{PROTON}/escalation/notify").mock(
        return_value=httpx.Response(200, json={"status": "ok"})
    )


# --- ship dark ------------------------------------------------------------


@respx.mock
async def test_with_the_flag_off_a_whatsapp_escalation_still_notifies_nobody(monkeypatch):
    _enable(monkeypatch, all_channels=False)
    notify = _stub(channel_type="Channel::Whatsapp")

    await sync.maybe_escalate(_payload())

    assert not notify.called
    get_proton_config_client.cache_clear()


@respx.mock
async def test_with_the_flag_off_an_email_escalation_behaves_exactly_as_today(monkeypatch):
    _enable(monkeypatch, all_channels=False)
    notify = _stub(channel_type="Channel::Email")

    await sync.maybe_escalate(_payload())

    assert notify.called
    get_proton_config_client.cache_clear()


# --- the fix --------------------------------------------------------------


@respx.mock
async def test_with_the_flag_on_a_whatsapp_escalation_reaches_the_backend(monkeypatch):
    _enable(monkeypatch, all_channels=True)
    notify = _stub(channel_type="Channel::Whatsapp")

    await sync.maybe_escalate(_payload())

    assert notify.called
    body = json.loads(notify.calls.last.request.read())
    assert body["channel_type"] == "Channel::Whatsapp"
    get_proton_config_client.cache_clear()


@respx.mock
async def test_with_the_flag_on_a_voice_escalation_reaches_the_backend(monkeypatch):
    """The PIC and dealer still need telling. Only the written customer ack
    has nowhere to go, and the backend is what decides that."""
    _enable(monkeypatch, all_channels=True)
    notify = _stub(channel_type="Channel::Voice")

    await sync.maybe_escalate(_payload())

    assert notify.called
    assert json.loads(notify.calls.last.request.read())["channel_type"] == "Channel::Voice"
    get_proton_config_client.cache_clear()


@respx.mock
async def test_with_the_flag_on_an_email_escalation_still_uses_the_email_transport(
    monkeypatch,
):
    _enable(monkeypatch, all_channels=True)
    notify = _stub(channel_type="Channel::Email")

    await sync.maybe_escalate(_payload())

    assert json.loads(notify.calls.last.request.read())["channel_type"] == "Channel::Email"
    get_proton_config_client.cache_clear()


@respx.mock
async def test_the_once_per_escalation_guard_holds_on_a_whatsapp_conversation(monkeypatch):
    _enable(monkeypatch, all_channels=True)
    notify = _stub(
        channel_type="Channel::Whatsapp",
        conv_attrs={"escalation_notified_at": "2026-08-08T00:00:00+00:00"},
    )

    await sync.maybe_escalate(_payload())

    assert not notify.called
    get_proton_config_client.cache_clear()


@respx.mock
async def test_the_guard_is_re_armed_when_the_escalate_label_is_removed_on_any_channel(
    monkeypatch,
):
    _enable(monkeypatch, all_channels=True)
    # set_custom_attributes reads before it writes -- the endpoint replaces the
    # whole object rather than merging, so a blind POST would clobber.
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/42").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 42,
                "inbox_id": 4,
                "custom_attributes": {
                    "escalation_notified_at": "2026-08-08T00:00:00+00:00"
                },
            },
        )
    )
    respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/custom_attributes").mock(
        return_value=httpx.Response(200, json={})
    )

    await sync.maybe_escalate(
        _payload(labels=(), attrs={"escalation_notified_at": "2026-08-08T00:00:00+00:00"})
    )

    cleared = [
        json.loads(c.request.read())
        for c in respx.calls
        if c.request.url.path.endswith("/conversations/42/custom_attributes")
    ]
    assert cleared and cleared[0]["custom_attributes"]["escalation_notified_at"] in (None, "")
    get_proton_config_client.cache_clear()


@respx.mock
async def test_an_inbox_fetch_failure_is_logged_and_skips_without_raising(monkeypatch):
    _enable(monkeypatch, all_channels=True)
    notify = _stub(channel_type="Channel::Whatsapp", inbox_raises=True)

    await sync.maybe_escalate(_payload())

    assert not notify.called
    get_proton_config_client.cache_clear()
