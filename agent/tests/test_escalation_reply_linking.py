"""Linking an internal (dealer/PIC) emailed reply onto the escalated conversation."""

import json

import httpx
import respx

from app.clients.deps import get_proton_config_client
from app.config import get_settings
from app.services import escalation_replies

CHATWOOT = "http://chatwoot-rails:3000"
PROTON = "http://proton-backend:8080"


def _payload(*, to="support+case42@test", sender="a@test", inbox_id=4, message_type="incoming"):
    return {
        "event": "message_created",
        "id": 900,
        "message_type": message_type,
        "content": "We fixed it.\n\nOn Thu, Support <s@t> wrote:\n> original",
        "conversation": {"id": 777},
        "inbox": {"id": inbox_id},
        "sender": {"email": sender, "name": "Komang"},
        "content_attributes": {"email": {"to": [to], "subject": "Re: [CASE-42] x"}},
    }


def _enable(monkeypatch):
    monkeypatch.setattr(get_settings(), "escalation_reply_linking_enabled", True)
    monkeypatch.setattr(get_settings(), "escalation_reply_draft_enabled", False)
    monkeypatch.setattr(get_settings(), "proton_backend_url", PROTON)
    monkeypatch.setattr(get_settings(), "proton_backend_key", "k")
    get_proton_config_client.cache_clear()


def _stub_chatwoot(*, conv_attrs=None):
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/inboxes/4").mock(
        return_value=httpx.Response(200, json={"id": 4, "channel_type": "Channel::Email"})
    )
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/42").mock(
        return_value=httpx.Response(
            200,
            json={"id": 42, "custom_attributes": conv_attrs or {},
                  "meta": {"sender": {"email": "customer@test"}}},
        )
    )
    respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/custom_attributes").mock(
        return_value=httpx.Response(200, json={})
    )
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/labels").mock(
        return_value=httpx.Response(200, json={"payload": []})
    )
    respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/labels").mock(
        return_value=httpx.Response(200, json={})
    )
    respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/777/toggle_status").mock(
        return_value=httpx.Response(200, json={})
    )
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/777/labels").mock(
        return_value=httpx.Response(200, json={"payload": []})
    )
    respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/777/labels").mock(
        return_value=httpx.Response(200, json={})
    )
    respx.get(f"{PROTON}/escalation/contacts").mock(
        return_value=httpx.Response(
            200, json={"contacts": [{"email": "a@test", "name": "Komang", "kind": "dealer"}]}
        )
    )
    return respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )


@respx.mock
async def test_posts_private_note_with_stripped_body(monkeypatch):
    _enable(monkeypatch)
    messages = _stub_chatwoot()

    await escalation_replies.maybe_link_escalation_reply(_payload())

    body = json.loads(messages.calls.last.request.read())
    assert body["private"] is True
    assert "We fixed it." in body["content"]
    assert "> original" not in body["content"]
    assert "Komang" in body["content"]
    get_proton_config_client.cache_clear()


@respx.mock
async def test_stamps_dealer_replied_at_and_labels(monkeypatch):
    _enable(monkeypatch)
    _stub_chatwoot()

    await escalation_replies.maybe_link_escalation_reply(_payload())

    posted = [
        json.loads(c.request.read())
        for c in respx.calls
        if c.request.url.path.endswith("/conversations/42/custom_attributes")
    ]
    assert posted and "dealer_replied_at" in posted[0]["custom_attributes"]
    labelled = [
        json.loads(c.request.read())
        for c in respx.calls
        if c.request.url.path.endswith("/conversations/42/labels")
        and c.request.method == "POST"
    ]
    assert labelled and "dealer_replied" in labelled[0]["labels"]
    get_proton_config_client.cache_clear()


@respx.mock
async def test_skips_unknown_sender(monkeypatch):
    _enable(monkeypatch)
    messages = _stub_chatwoot()

    await escalation_replies.maybe_link_escalation_reply(_payload(sender="stranger@test"))

    assert not messages.called
    get_proton_config_client.cache_clear()


@respx.mock
async def test_skips_when_contacts_unavailable(monkeypatch):
    _enable(monkeypatch)
    messages = _stub_chatwoot()
    respx.get(f"{PROTON}/escalation/contacts").mock(return_value=httpx.Response(500))

    await escalation_replies.maybe_link_escalation_reply(_payload())

    assert not messages.called
    get_proton_config_client.cache_clear()


@respx.mock
async def test_skips_when_already_stamped(monkeypatch):
    _enable(monkeypatch)
    messages = _stub_chatwoot(conv_attrs={"dealer_replied_at": "2026-08-06T00:00:00+00:00"})

    await escalation_replies.maybe_link_escalation_reply(_payload())

    assert not messages.called
    get_proton_config_client.cache_clear()


@respx.mock
async def test_ignores_outgoing_messages(monkeypatch):
    _enable(monkeypatch)
    messages = _stub_chatwoot()

    await escalation_replies.maybe_link_escalation_reply(_payload(message_type="outgoing"))

    assert not messages.called
    get_proton_config_client.cache_clear()


@respx.mock
async def test_noop_when_flag_disabled(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(get_settings(), "escalation_reply_linking_enabled", False)
    messages = _stub_chatwoot()

    await escalation_replies.maybe_link_escalation_reply(_payload())

    assert not messages.called
    get_proton_config_client.cache_clear()


@respx.mock
async def test_noop_without_token(monkeypatch):
    _enable(monkeypatch)
    messages = _stub_chatwoot()
    payload = _payload(to="support@test")
    payload["content_attributes"]["email"]["subject"] = "Re: no tag here"

    await escalation_replies.maybe_link_escalation_reply(payload)

    assert not messages.called
    get_proton_config_client.cache_clear()
