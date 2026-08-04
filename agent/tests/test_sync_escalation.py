"""Tests for `sync.maybe_escalate` (EM-7 email-channel escalation
notification) and `sync.maybe_stamp_dealer_escalation` (dealer-label
escalation timestamping for reporting).

Ticket-creation behavior was removed along with the external ticketing
backend; only the Chatwoot-native escalation paths remain.
"""

import json

import httpx
import respx

from app.clients.deps import get_proton_config_client
from app.config import get_settings
from app.services import sync

CHATWOOT = "http://chatwoot-rails:3000"
PROTON = "http://proton-backend:8080"

MESSAGES_RESPONSE = {
    "payload": [
        {
            "id": 1,
            "content": "Hi, my invoice is wrong",
            "message_type": 0,
            "private": False,
            "created_at": 1_700_000_000,
            "sender": {"id": 55, "name": "Jane Doe", "email": "jane@example.com"},
        },
        {
            "id": 2,
            "content": "internal note, ignore",
            "message_type": 1,
            "private": True,
            "created_at": 1_700_000_100,
            "sender": {"id": 9, "name": "Agent Bob"},
        },
        {
            "id": 3,
            "content": "Looking into it now",
            "message_type": 1,
            "private": False,
            "created_at": 1_700_000_200,
            "sender": {"id": 9, "name": "Agent Bob"},
        },
    ]
}


@respx.mock
async def test_maybe_escalate_ignores_payload_without_escalate_label(monkeypatch):
    monkeypatch.setattr(get_settings(), "email_escalation_enabled", True)
    notify_route = respx.post(f"{PROTON}/escalation/notify")

    payload = {"event": "conversation_updated", "id": 43, "labels": ["billing"]}
    await sync.maybe_escalate(payload)

    assert not notify_route.called


@respx.mock
async def test_stamps_dealer_escalated_at_on_first_dealer_label():
    """The first time a `dealer_<slug>` label appears on a conversation,
    `maybe_stamp_dealer_escalation` stamps `dealer_escalated_at` so the BI
    turnaround-time view has a real escalation timestamp to diff against
    `resolved_at`."""
    get_conversation = respx.get(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/10"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 10,
                "custom_attributes": {"demo_seed": "batch-1", "case_category": "Sales"},
            },
        )
    )
    set_attrs = respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/10/custom_attributes"
    ).mock(return_value=httpx.Response(200, json={}))

    payload = {"id": 10, "labels": ["division_sales", "dealer_kl_glenmarie"]}
    await sync.maybe_stamp_dealer_escalation(payload)

    # Two GETs: this handler's own "is it already stamped?" read, plus the
    # read `ChatwootClient.set_custom_attributes` does to merge rather than
    # replace (Chatwoot's endpoint assigns the whole object).
    assert get_conversation.call_count == 2
    assert set_attrs.call_count == 1
    body = json.loads(set_attrs.calls.last.request.content)
    assert "dealer_escalated_at" in body["custom_attributes"]
    # ...and the stamp must not have erased anything already on the row.
    assert body["custom_attributes"]["demo_seed"] == "batch-1"
    assert body["custom_attributes"]["case_category"] == "Sales"


@respx.mock
async def test_no_dealer_label_no_op():
    set_attrs = respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/11/custom_attributes"
    ).mock(return_value=httpx.Response(200, json={}))

    payload = {"id": 11, "labels": ["division_sales"]}
    await sync.maybe_stamp_dealer_escalation(payload)

    assert not set_attrs.called


@respx.mock
async def test_already_stamped_never_overwritten():
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/12").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 12,
                "custom_attributes": {
                    "dealer_escalated_at": "2026-07-01T00:00:00+00:00"
                },
            },
        )
    )
    set_attrs = respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/12/custom_attributes"
    ).mock(return_value=httpx.Response(200, json={}))

    payload = {"id": 12, "labels": ["dealer_kl_glenmarie"]}
    await sync.maybe_stamp_dealer_escalation(payload)

    assert not set_attrs.called


@respx.mock
async def test_missing_conversation_id_no_op():
    set_attrs = respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/13/custom_attributes"
    ).mock(return_value=httpx.Response(200, json={}))

    await sync.maybe_stamp_dealer_escalation({"labels": ["dealer_kl_glenmarie"]})

    assert not set_attrs.called


@respx.mock
async def test_maybe_escalate_notifies_email_channel_conversation(monkeypatch):
    """EM-7: an Email-channel conversation escalated fires the backend
    two-thread email notification with the `dept_`/`dealer_` labels parsed
    out."""
    monkeypatch.setattr(get_settings(), "email_escalation_enabled", True)
    monkeypatch.setattr(get_settings(), "proton_backend_url", "http://proton-backend:8080")
    monkeypatch.setattr(get_settings(), "proton_backend_key", "k")
    get_proton_config_client.cache_clear()

    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/9").mock(
        return_value=httpx.Response(200, json={"id": 9, "inbox_id": 5})
    )
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/inboxes/5").mock(
        return_value=httpx.Response(200, json={"id": 5, "channel_type": "Channel::Email"})
    )
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/9/messages").mock(
        return_value=httpx.Response(200, json=MESSAGES_RESPONSE)
    )
    notify_route = respx.post(f"{PROTON}/escalation/notify").mock(
        return_value=httpx.Response(200, json={"status": "ok"})
    )

    await sync.maybe_escalate(
        {"id": 9, "labels": ["escalate", "dept_apps", "dealer_kl_pj"]}
    )

    assert notify_route.called
    sent = json.loads(notify_route.calls[0].request.content)
    assert sent["conversation_id"] == "9"
    assert sent["department"] == "apps"
    assert sent["dealer"] == "kl_pj"
    get_proton_config_client.cache_clear()


@respx.mock
async def test_maybe_escalate_notify_includes_real_case_content(monkeypatch):
    """EM-7 fix: the notify title/body must carry real case content from the
    conversation transcript, not the generic 'was escalated by an agent'
    placeholder."""
    monkeypatch.setattr(get_settings(), "email_escalation_enabled", True)
    monkeypatch.setattr(get_settings(), "proton_backend_url", "http://proton-backend:8080")
    monkeypatch.setattr(get_settings(), "proton_backend_key", "k")
    get_proton_config_client.cache_clear()

    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/9").mock(
        return_value=httpx.Response(200, json={"id": 9, "inbox_id": 5})
    )
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/inboxes/5").mock(
        return_value=httpx.Response(200, json={"id": 5, "channel_type": "Channel::Email"})
    )
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/9/messages").mock(
        return_value=httpx.Response(200, json=MESSAGES_RESPONSE)
    )
    notify_route = respx.post(f"{PROTON}/escalation/notify").mock(
        return_value=httpx.Response(200, json={"status": "ok"})
    )

    await sync.maybe_escalate({"id": 9, "labels": ["escalate"]})

    assert notify_route.called
    sent = json.loads(notify_route.calls[0].request.content)
    assert sent["title"] == "Hi, my invoice is wrong"
    assert "Jane Doe: Hi, my invoice is wrong" in sent["body"]
    assert "Agent Bob: Looking into it now" in sent["body"]
    # the private internal note must never leak into the email body
    assert "internal note, ignore" not in sent["body"]
    get_proton_config_client.cache_clear()


@respx.mock
async def test_maybe_escalate_notify_falls_back_when_messages_fetch_fails(monkeypatch):
    """If the transcript fetch fails, the notification must still fire with
    the generic fallback title/body -- a failure to build a nice transcript
    must never silently drop the escalation email."""
    monkeypatch.setattr(get_settings(), "email_escalation_enabled", True)
    monkeypatch.setattr(get_settings(), "proton_backend_url", "http://proton-backend:8080")
    monkeypatch.setattr(get_settings(), "proton_backend_key", "k")
    get_proton_config_client.cache_clear()

    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/9").mock(
        return_value=httpx.Response(200, json={"id": 9, "inbox_id": 5})
    )
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/inboxes/5").mock(
        return_value=httpx.Response(200, json={"id": 5, "channel_type": "Channel::Email"})
    )
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/9/messages").mock(
        return_value=httpx.Response(500, json={"error": "boom"})
    )
    notify_route = respx.post(f"{PROTON}/escalation/notify").mock(
        return_value=httpx.Response(200, json={"status": "ok"})
    )

    await sync.maybe_escalate({"id": 9, "labels": ["escalate"]})

    assert notify_route.called
    sent = json.loads(notify_route.calls[0].request.content)
    assert sent["title"] == "Escalated conversation #9"
    assert sent["body"] == "Conversation #9 was escalated by an agent."
    get_proton_config_client.cache_clear()


@respx.mock
async def test_maybe_escalate_skips_notify_for_non_email_channel(monkeypatch):
    """A non-Email-channel conversation (e.g. SMS) must never trigger the
    EM-7 two-thread email escalation -- it's a Chatwoot-native flow only."""
    monkeypatch.setattr(get_settings(), "email_escalation_enabled", True)
    monkeypatch.setattr(get_settings(), "proton_backend_url", "http://proton-backend:8080")
    monkeypatch.setattr(get_settings(), "proton_backend_key", "k")
    get_proton_config_client.cache_clear()

    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/9").mock(
        return_value=httpx.Response(200, json={"id": 9, "inbox_id": 3})
    )
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/inboxes/3").mock(
        return_value=httpx.Response(200, json={"id": 3, "channel_type": "Channel::TwilioSms"})
    )
    notify_route = respx.post(f"{PROTON}/escalation/notify").mock(
        return_value=httpx.Response(200, json={"status": "ok"})
    )

    await sync.maybe_escalate({"id": 9, "labels": ["escalate"]})

    assert not notify_route.called
    get_proton_config_client.cache_clear()


@respx.mock
async def test_maybe_escalate_skips_notify_when_flag_off(monkeypatch):
    """`email_escalation_enabled` defaults False -- the helper must return
    before making any Chatwoot call (byte-identical no-op when unset)."""
    monkeypatch.setattr(get_settings(), "email_escalation_enabled", False)

    conv_route = respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/9")

    await sync.maybe_escalate({"id": 9, "labels": ["escalate"]})

    assert not conv_route.called
