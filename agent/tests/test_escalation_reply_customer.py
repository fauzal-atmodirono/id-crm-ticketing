"""A customer's reply to the escalation acknowledgement lands on the original.

Chatwoot only accepts `message_type=incoming` on Api-channel inboxes -- on a
Channel::Email inbox (the only channel this reply loop ever runs on) it
rejects the post with a 422 ("Incoming messages are only allowed in Api
inboxes"). The linker always attempts the incoming post first (it is correct
and would succeed on an Api inbox), then falls back to a private note when
Chatwoot rejects it, and reopens the conversation either way so it comes back
to the agent queue.
"""

import json

import httpx
import respx

from app.clients.deps import get_proton_config_client
from app.config import get_settings
from app.services import escalation_replies

CHATWOOT = "http://chatwoot-rails:3000"
PROTON = "http://proton-backend:8080"


def _payload(sender="customer@test", content="Any update?"):
    return {
        "event": "message_created",
        "id": 901,
        "message_type": "incoming",
        "content": content,
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


def _stub(messages_response=None):
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
    respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/toggle_status").mock(
        return_value=httpx.Response(200, json={})
    )
    kwargs = {}
    if messages_response is not None:
        kwargs["side_effect"] = messages_response
    else:
        kwargs["return_value"] = httpx.Response(200, json={"id": 2})
    return respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages").mock(**kwargs)


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
async def test_customer_reply_success_reopens_conversation_and_does_not_double_post(
    monkeypatch,
):
    """When the incoming post succeeds there must be no fallback private
    note -- only one message call to the case."""
    _enable(monkeypatch)
    messages = _stub()

    await escalation_replies.maybe_link_escalation_reply(_payload())

    assert len(messages.calls) == 1
    toggles = [
        c for c in respx.calls
        if c.request.url.path.endswith("/conversations/42/toggle_status")
    ]
    assert toggles
    assert json.loads(toggles[0].request.read())["status"] == "open"
    get_proton_config_client.cache_clear()


@respx.mock
async def test_customer_reply_422_falls_back_to_private_note_and_reopens(monkeypatch):
    """Chatwoot rejects `message_type=incoming` on a Channel::Email inbox
    with a 422 ("Incoming messages are only allowed in Api inboxes"). The
    linker must fall back to a private note carrying the customer's own
    text, reopen the conversation, and still tidy up the throwaway
    conversation the reply landed in."""
    _enable(monkeypatch)
    messages = _stub(
        messages_response=[
            httpx.Response(
                422, json={"error": "Incoming messages are only allowed in Api inboxes"}
            ),
            httpx.Response(200, json={"id": 3}),
        ]
    )

    await escalation_replies.maybe_link_escalation_reply(_payload())

    assert len(messages.calls) == 2
    first_attempt = json.loads(messages.calls[0].request.read())
    assert first_attempt["private"] is False
    assert first_attempt["message_type"] == "incoming"

    fallback = json.loads(messages.calls[1].request.read())
    assert fallback["private"] is True
    assert "message_type" not in fallback
    assert "Any update?" in fallback["content"]
    # Distinct from the internal/dealer note prefix ("Reply from ...") so
    # nobody mistakes the customer's own words for an agent note or a
    # dealer reply.
    assert not fallback["content"].startswith("Reply from")
    assert "customer@test" in fallback["content"]

    toggles = [
        c for c in respx.calls
        if c.request.url.path.endswith("/conversations/42/toggle_status")
    ]
    assert toggles
    assert json.loads(toggles[0].request.read())["status"] == "open"

    # The throwaway conversation is still tidied up even though the
    # incoming post failed.
    labelled = [
        label
        for c in respx.calls
        if c.request.method == "POST"
        and c.request.url.path.endswith("/778/labels")
        for label in json.loads(c.request.read())["labels"]
    ]
    assert "escalation_reply" in labelled
    resolved = [
        c for c in respx.calls
        if c.request.url.path.endswith("/778/toggle_status")
    ]
    assert resolved
    assert json.loads(resolved[0].request.read())["status"] == "resolved"
    get_proton_config_client.cache_clear()


@respx.mock
async def test_customer_reply_does_not_stamp_escalation_replied_at(monkeypatch):
    _enable(monkeypatch)
    _stub()

    await escalation_replies.maybe_link_escalation_reply(_payload())

    assert not [
        c for c in respx.calls
        if c.request.url.path.endswith("/conversations/42/custom_attributes")
    ]
    get_proton_config_client.cache_clear()


@respx.mock
async def test_customer_reply_422_fallback_still_does_not_stamp_or_label_the_case(
    monkeypatch,
):
    """A customer may reply many times, so the fallback path must not gate
    future replies the way the internal note does -- no stamp, no
    `escalation_replied` label on the case itself."""
    _enable(monkeypatch)
    messages = _stub(
        messages_response=[
            httpx.Response(422, json={"error": "..."}),
            httpx.Response(200, json={"id": 3}),
        ]
    )

    await escalation_replies.maybe_link_escalation_reply(_payload())

    assert not [
        c for c in respx.calls
        if c.request.url.path.endswith("/conversations/42/custom_attributes")
    ]
    case_labels = [
        c for c in respx.calls
        if c.request.method == "POST" and c.request.url.path.endswith("/42/labels")
    ]
    assert not case_labels
    assert len(messages.calls) == 2
    get_proton_config_client.cache_clear()


@respx.mock
async def test_customer_reply_twice_both_land(monkeypatch):
    """A customer may reply more than once; nothing gates the second one.

    Pins down the premise this task exists to satisfy -- if the stamp check
    were ever moved back to cover the customer branch, this is the test that
    would catch it (every other test in this file only sends one reply).
    """
    _enable(monkeypatch)
    messages = _stub()

    await escalation_replies.maybe_link_escalation_reply(_payload(content="Any update?"))
    await escalation_replies.maybe_link_escalation_reply(_payload(content="Still waiting."))

    assert len(messages.calls) == 2
    bodies = [json.loads(c.request.read()) for c in messages.calls]
    for body in bodies:
        assert body["private"] is False
        assert body["message_type"] == "incoming"
    assert bodies[0]["content"] == "Any update?"
    assert bodies[1]["content"] == "Still waiting."
    get_proton_config_client.cache_clear()


@respx.mock
async def test_customer_reply_still_skipped_when_email_does_not_match(monkeypatch):
    _enable(monkeypatch)
    messages = _stub()

    await escalation_replies.maybe_link_escalation_reply(_payload(sender="stranger@test"))

    # Not treated as the customer and not linked; surfaced as a pointer note
    # naming the address, without the body (see test_escalation_reply_linking).
    assert messages.called
    assert "stranger@test" in messages.calls[0].request.content.decode()
    get_proton_config_client.cache_clear()
