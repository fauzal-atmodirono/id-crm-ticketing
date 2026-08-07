"""Side effects the escalation reply loop must NOT have on the throwaway
conversation the reply lands in.

A dealer's reply arrives as a brand-new Email-channel conversation, and two
customer-facing automations fire on it purely because it looks like a fresh
customer email:

  - `lifecycle.on_conversation_created` posts the "Dear Customer" SOP
    auto-acknowledgement;
  - the linker's own `toggle_status(..., "resolved")` fires
    `conversation_resolved`, and `lifecycle.on_human_resolved` answers it with
    the public agent-performance survey -- emailing an external dealer a
    request to rate a Proton agent 1-5.

Both are wrong and both are covered here.
"""

from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from app.clients.deps import get_proton_config_client
from app.config import get_settings
from app.services import escalation_replies, lifecycle, lifecycle_store

CHATWOOT = "http://chatwoot-rails:3000"
PROTON = "http://proton-backend:8080"

REPLY_CONV_ID = 777


def _payload():
    return {
        "event": "message_created",
        "id": 900,
        "message_type": "incoming",
        "content": "We fixed it.",
        "conversation": {"id": REPLY_CONV_ID},
        "inbox": {"id": 4},
        "sender": {"email": "a@test", "name": "Komang"},
        "content_attributes": {"email": {"to": ["support+case42@test"]}},
    }


def _enable(monkeypatch):
    monkeypatch.setattr(get_settings(), "escalation_reply_linking_enabled", True)
    monkeypatch.setattr(get_settings(), "escalation_reply_draft_enabled", False)
    monkeypatch.setattr(get_settings(), "proton_backend_url", PROTON)
    monkeypatch.setattr(get_settings(), "proton_backend_key", "k")
    monkeypatch.setattr(get_settings(), "lifecycle_survey_enabled", True)
    get_proton_config_client.cache_clear()


def _stub_chatwoot():
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/inboxes/4").mock(
        return_value=httpx.Response(200, json={"id": 4, "channel_type": "Channel::Email"})
    )
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/42").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 42,
                "custom_attributes": {},
                "meta": {"sender": {"email": "customer@test"}},
            },
        )
    )
    respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/custom_attributes").mock(
        return_value=httpx.Response(200, json={})
    )
    for conv in (42, REPLY_CONV_ID):
        respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/{conv}/labels").mock(
            return_value=httpx.Response(200, json={"payload": []})
        )
        respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/{conv}/labels").mock(
            return_value=httpx.Response(200, json={})
        )
    respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/{REPLY_CONV_ID}/toggle_status"
    ).mock(return_value=httpx.Response(200, json={}))
    respx.get(f"{PROTON}/escalation/contacts").mock(
        return_value=httpx.Response(
            200,
            json={"contacts": [{"email": "a@test", "name": "Komang", "kind": "dealer"}]},
        )
    )
    return respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/{REPLY_CONV_ID}/messages"
    ).mock(return_value=httpx.Response(200, json={"id": 2}))


@respx.mock
async def test_linker_closes_the_throwaway_conversations_lifecycle(monkeypatch):
    """The throwaway conversation was seeded ACTIVE by `on_conversation_created`.
    The linker must move it to CLOSED before resolving it, so the resulting
    `conversation_resolved` webhook hits `on_human_resolved`'s terminal-state
    guard instead of starting a survey."""
    _enable(monkeypatch)
    _stub_chatwoot()
    await lifecycle_store.seed_active(REPLY_CONV_ID, channel="Channel::Email")

    await escalation_replies.maybe_link_escalation_reply(_payload())

    assert await lifecycle_store.get_state(REPLY_CONV_ID) == lifecycle.CLOSED
    get_proton_config_client.cache_clear()


@respx.mock
async def test_dealer_never_gets_the_agent_rating_survey(monkeypatch):
    """End to end: link the reply, then replay the `conversation_resolved`
    webhook the linker's own toggle_status produces. Nothing may be posted
    back into the dealer's thread."""
    _enable(monkeypatch)
    reply_messages = _stub_chatwoot()
    await lifecycle_store.seed_active(REPLY_CONV_ID, channel="Channel::Email")

    await escalation_replies.maybe_link_escalation_reply(_payload())
    await lifecycle.on_human_resolved(
        {"id": REPLY_CONV_ID, "status": "resolved", "inbox_id": 4}
    )

    assert not reply_messages.called
    get_proton_config_client.cache_clear()


@respx.mock
async def test_lifecycle_close_failure_still_resolves_the_reply_conversation(
    monkeypatch,
):
    """Fail-open: a DB error while closing the lifecycle row must not stop the
    reply being linked or the throwaway conversation being tidied away."""
    _enable(monkeypatch)
    _stub_chatwoot()
    toggle = respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/{REPLY_CONV_ID}/toggle_status"
    )

    async def _boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(escalation_replies.lifecycle_store, "transition", _boom)

    await escalation_replies.maybe_link_escalation_reply(_payload())

    assert toggle.called
    get_proton_config_client.cache_clear()


# --- auto-acknowledgement suppression -------------------------------------


@pytest.fixture
def chatwoot(monkeypatch):
    client = AsyncMock()
    monkeypatch.setattr(lifecycle, "get_chatwoot_client", lambda: client)
    monkeypatch.setattr(lifecycle, "get_proton_config_client", lambda: None)
    client.get_inbox.return_value = {"channel_type": "Channel::Email"}
    return client


@pytest.fixture
def autoack_on(monkeypatch):
    monkeypatch.setattr(get_settings(), "email_autoack_enabled", True, raising=False)
    monkeypatch.setattr(
        get_settings(), "lifecycle_disclaimer_enabled", True, raising=False
    )


def _created_payload(conversation_id, *, to, subject="Re: your case"):
    return {
        "id": conversation_id,
        "inbox_id": 4,
        "channel": "Channel::Email",
        "messages": [
            {
                "id": 1,
                "message_type": 0,
                "content": "We fixed it.",
                "content_attributes": {"email": {"to": [to], "subject": subject}},
            }
        ],
    }


async def test_autoack_suppressed_when_first_message_carries_a_case_token(
    chatwoot, autoack_on
):
    """A dealer/PIC replying to an escalation is not a customer. Sending them
    the customer-facing "Dear Customer" SOP boilerplate (business hours, the
    call-centre number) is wrong, and it is the same root cause as the survey
    above: Chatwoot cannot tell this conversation apart from a fresh enquiry,
    but the correlation token can."""
    await lifecycle.on_conversation_created(
        _created_payload(880, to="support+case42@test")
    )

    assert await lifecycle_store.get_state(880) == lifecycle.ACTIVE
    chatwoot.create_message.assert_not_awaited()


async def test_autoack_suppressed_via_the_subject_tag_fallback(chatwoot, autoack_on):
    """Same, for relays that strip plus-addressing and leave only `[CASE-N]`."""
    await lifecycle.on_conversation_created(
        _created_payload(881, to="support@test", subject="Re: [CASE-42] engine fault")
    )

    chatwoot.create_message.assert_not_awaited()


async def test_autoack_suppressed_from_mail_subject_without_a_messages_array(
    chatwoot, autoack_on
):
    """The load-bearing signal on a real deploy.

    Chatwoot writes `additional_attributes.mail_subject` as it creates the
    conversation from the inbound mail, whereas the `messages` array holds the
    conversation's last message at dispatch time -- and an inbound email's
    message row is written just after the conversation's, so the array can
    legitimately be empty on this event. Every internal escalation mail
    carries the `[CASE-n]` subject tag, so the dealer/PIC case (the one that
    puts "Dear Customer" in an external mailbox) must be caught with no
    `messages` at all.
    """
    await lifecycle.on_conversation_created(
        {
            "id": 884,
            "inbox_id": 4,
            "channel": "Channel::Email",
            "additional_attributes": {"mail_subject": "Re: [CASE-42] engine fault"},
        }
    )

    chatwoot.create_message.assert_not_awaited()


async def test_autoack_still_sent_for_a_genuine_first_contact_email(
    chatwoot, autoack_on
):
    """The suppression must be narrow: a real customer's first email carries no
    correlation token and still gets its acknowledgement."""
    await lifecycle.on_conversation_created(
        _created_payload(882, to="support@test", subject="My car will not start")
    )

    chatwoot.create_message.assert_awaited_once()
    args, _ = chatwoot.create_message.await_args
    assert "acknowledge receipt of your enquiry" in args[1]


async def test_autoack_still_sent_when_the_payload_carries_no_messages(
    chatwoot, autoack_on
):
    """Fail-open on payload shape: no `messages` array means no evidence this
    is a reply, so the acknowledgement goes out exactly as it does today."""
    await lifecycle.on_conversation_created(
        {
            "id": 883,
            "inbox_id": 4,
            "channel": "Channel::Email",
            "additional_attributes": {"mail_subject": "My car will not start"},
        }
    )

    chatwoot.create_message.assert_awaited_once()
