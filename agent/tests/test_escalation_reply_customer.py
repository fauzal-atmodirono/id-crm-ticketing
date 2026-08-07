"""A customer's reply to the escalation acknowledgement lands on the original."""

import json

import httpx
import respx

from app.clients.deps import get_proton_config_client
from app.config import get_settings
from app.services import escalation_replies

CHATWOOT = "http://chatwoot-rails:3000"
PROTON = "http://proton-backend:8080"


def _payload(sender="customer@test"):
    return {
        "event": "message_created",
        "id": 901,
        "message_type": "incoming",
        "content": "Any update?",
        "conversation": {"id": 778},
        "inbox": {"id": 4},
        "sender": {"email": sender, "name": "Jane"},
        "content_attributes": {"email": {"to": ["support+case42@test"]}},
    }


def _enable(monkeypatch):
    monkeypatch.setattr(get_settings(), "escalation_reply_linking_enabled", True)
    monkeypatch.setattr(get_settings(), "escalation_reply_draft_enabled", False)
    monkeypatch.setattr(get_settings(), "proton_backend_url", PROTON)
    monkeypatch.setattr(get_settings(), "proton_backend_key", "k")
    get_proton_config_client.cache_clear()


def _stub():
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/inboxes/4").mock(
        return_value=httpx.Response(200, json={"id": 4, "channel_type": "Channel::Email"})
    )
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/42").mock(
        return_value=httpx.Response(
            200,
            json={"id": 42, "custom_attributes": {},
                  "meta": {"sender": {"email": "customer@test"}}},
        )
    )
    respx.get(f"{PROTON}/escalation/contacts").mock(
        return_value=httpx.Response(
            200, json={"contacts": [{"email": "a@test", "name": "Komang", "kind": "dealer"}]}
        )
    )
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/778/labels").mock(
        return_value=httpx.Response(200, json={"payload": []})
    )
    respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/778/labels").mock(
        return_value=httpx.Response(200, json={})
    )
    respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/778/toggle_status").mock(
        return_value=httpx.Response(200, json={})
    )
    return respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages").mock(
        return_value=httpx.Response(200, json={"id": 2})
    )


@respx.mock
async def test_customer_reply_posts_public_incoming_message(monkeypatch):
    _enable(monkeypatch)
    messages = _stub()

    await escalation_replies.maybe_link_escalation_reply(_payload())

    body = json.loads(messages.calls.last.request.read())
    assert body["private"] is False
    assert body["message_type"] == "incoming"
    assert body["content"] == "Any update?"
    get_proton_config_client.cache_clear()


@respx.mock
async def test_customer_reply_does_not_stamp_dealer_replied_at(monkeypatch):
    _enable(monkeypatch)
    _stub()

    await escalation_replies.maybe_link_escalation_reply(_payload())

    assert not [
        c for c in respx.calls
        if c.request.url.path.endswith("/conversations/42/custom_attributes")
    ]
    get_proton_config_client.cache_clear()


@respx.mock
async def test_customer_reply_still_skipped_when_email_does_not_match(monkeypatch):
    _enable(monkeypatch)
    messages = _stub()

    await escalation_replies.maybe_link_escalation_reply(_payload(sender="stranger@test"))

    assert not messages.called
    get_proton_config_client.cache_clear()
