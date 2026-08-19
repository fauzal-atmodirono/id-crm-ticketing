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
async def test_maybe_escalate_sends_a_clean_customer_subject(monkeypatch):
    """The customer leg must not be sent the customer's own words back.

    `title` is the first ~100 characters of the customer's first message --
    right for the PIC/dealer inboxes, wrong for the customer, who on
    2026-08-19 received "Update on your case: Hi, I bought an e.MAS 7 ...
    The home charger" with the sentence cut mid-word.
    """
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
    respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/9/custom_attributes").mock(
        return_value=httpx.Response(200, json={})
    )

    await sync.maybe_escalate({"id": 9, "labels": ["escalate", "dept_apps"]})

    sent = json.loads(notify_route.calls[0].request.content)
    assert sent["customer_subject"] == "Update on your case (#9)"
    # ...while the internal legs keep the descriptive title.
    assert sent["title"] == "Hi, my invoice is wrong"
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
async def test_maybe_escalate_notify_title_is_single_line(monkeypatch):
    """The title becomes an email Subject header downstream, and
    `EmailMessage.__setitem__` raises ValueError on a value containing CR/LF
    ("values may not contain linefeed or carriage return characters").

    An Email-channel conversation's first incoming message IS the raw email
    body, which is virtually always multi-line -- so an unsanitised title made
    every real email escalation fail to send, with the exception escaping
    SmtpEmailSender.send() (message construction sits outside its try) and
    surfacing only as a swallowed `escalation_email_failed` warning.
    """
    monkeypatch.setattr(get_settings(), "email_escalation_enabled", True)
    monkeypatch.setattr(get_settings(), "proton_backend_url", "http://proton-backend:8080")
    monkeypatch.setattr(get_settings(), "proton_backend_key", "k")
    get_proton_config_client.cache_clear()

    multiline = {
        "payload": [
            {
                "id": 1,
                "content": "Test escalation TC-01 - my car will not start\r\n\r\nSent from my iPhone",
                "message_type": 0,
                "private": False,
                "created_at": 1_700_000_000,
                "sender": {"id": 55, "name": "Jane Doe", "email": "jane@example.com"},
            },
        ]
    }

    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/9").mock(
        return_value=httpx.Response(200, json={"id": 9, "inbox_id": 5})
    )
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/inboxes/5").mock(
        return_value=httpx.Response(200, json={"id": 5, "channel_type": "Channel::Email"})
    )
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/9/messages").mock(
        return_value=httpx.Response(200, json=multiline)
    )
    notify_route = respx.post(f"{PROTON}/escalation/notify").mock(
        return_value=httpx.Response(200, json={"status": "ok"})
    )

    await sync.maybe_escalate({"id": 9, "labels": ["escalate"]})

    assert notify_route.called
    title = json.loads(notify_route.calls[0].request.content)["title"]
    assert "\n" not in title and "\r" not in title
    # the leading line still identifies the case
    assert title.startswith("Test escalation TC-01 - my car will not start")
    # the body keeps the full multi-line text -- only the header is constrained
    assert "Sent from my iPhone" in json.loads(notify_route.calls[0].request.content)["body"]
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
    """With ESCALATION_ALL_CHANNELS_ENABLED off, a non-Email conversation
    (e.g. SMS) must not trigger the EM-7 escalation -- the pre-P2 behaviour.

    The flag is pinned rather than left to its default: this test asserts one
    side of it, and an env var set in the shell would otherwise decide the
    answer for it. P2's own suite covers the on-side.
    """
    monkeypatch.setattr(get_settings(), "email_escalation_enabled", True)
    monkeypatch.setattr(get_settings(), "escalation_all_channels_enabled", False)
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


def _stateful_email_conversation(monkeypatch, conv_id=9, attrs=None):
    """Stand in for a real Chatwoot Email-channel conversation whose
    `custom_attributes` actually persist across calls, so the once-per-
    escalation guard can be exercised end to end (read → notify → stamp →
    read again) instead of being hand-fed a pre-stamped fixture.
    """
    monkeypatch.setattr(get_settings(), "email_escalation_enabled", True)
    monkeypatch.setattr(get_settings(), "proton_backend_url", "http://proton-backend:8080")
    monkeypatch.setattr(get_settings(), "proton_backend_key", "k")
    get_proton_config_client.cache_clear()

    state = {"custom_attributes": dict(attrs or {})}

    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/{conv_id}").mock(
        side_effect=lambda request: httpx.Response(
            200,
            json={
                "id": conv_id,
                "inbox_id": 5,
                "custom_attributes": dict(state["custom_attributes"]),
            },
        )
    )

    def _write_attrs(request):
        state["custom_attributes"] = json.loads(request.content)["custom_attributes"]
        return httpx.Response(200, json={})

    respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/{conv_id}/custom_attributes"
    ).mock(side_effect=_write_attrs)
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/inboxes/5").mock(
        return_value=httpx.Response(200, json={"id": 5, "channel_type": "Channel::Email"})
    )
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/{conv_id}/messages").mock(
        return_value=httpx.Response(200, json=MESSAGES_RESPONSE)
    )
    return state


@respx.mock
async def test_maybe_escalate_notifies_once_per_escalation(monkeypatch):
    """A second `conversation_updated` on a still-escalated conversation must
    NOT re-fire the EM-7 fan-out.

    Without a guard, every write the reply linker makes to the escalated
    conversation (its `escalation_replied_at` stamp, its label, the reopen on
    the customer branch) fires `conversation_updated` while the `escalate`
    label is still present -- re-sending a real `Update on your case:` email
    to the end customer plus a duplicate PIC/dealer forward, automatically,
    on every reply.
    """
    state = _stateful_email_conversation(monkeypatch)
    notify_route = respx.post(f"{PROTON}/escalation/notify").mock(
        return_value=httpx.Response(200, json={"status": "ok"})
    )

    payload = {"id": 9, "labels": ["escalate", "dept_apps"]}
    await sync.maybe_escalate(payload)
    await sync.maybe_escalate(payload)

    assert notify_route.call_count == 1
    assert state["custom_attributes"].get("escalation_notified_at")
    get_proton_config_client.cache_clear()


@respx.mock
async def test_maybe_escalate_stamps_only_after_the_notify_call(monkeypatch):
    """Stamp-after-notify, never before: the stamp write must come after the
    backend call in wire order, so a notify that never happens cannot leave a
    stamp behind that permanently suppresses the escalation."""
    _stateful_email_conversation(monkeypatch)
    respx.post(f"{PROTON}/escalation/notify").mock(
        return_value=httpx.Response(200, json={"status": "ok"})
    )

    await sync.maybe_escalate({"id": 9, "labels": ["escalate"]})

    order = [
        (c.request.method, c.request.url.path)
        for c in respx.calls
        if c.request.url.path.endswith("/escalation/notify")
        or (
            c.request.method == "POST"
            and c.request.url.path.endswith("/conversations/9/custom_attributes")
        )
    ]
    assert [p for _, p in order] == [
        "/escalation/notify",
        "/api/v1/accounts/1/conversations/9/custom_attributes",
    ]
    get_proton_config_client.cache_clear()


@respx.mock
async def test_maybe_escalate_does_not_stamp_when_notify_fails(monkeypatch):
    """A failed send must leave the conversation un-stamped so the next
    `conversation_updated` still gets a chance to escalate -- silently losing
    a customer escalation is far worse than a duplicate email."""
    state = _stateful_email_conversation(monkeypatch)
    notify_route = respx.post(f"{PROTON}/escalation/notify").mock(
        return_value=httpx.Response(503)
    )

    await sync.maybe_escalate({"id": 9, "labels": ["escalate"]})

    assert notify_route.call_count == 1
    assert "escalation_notified_at" not in state["custom_attributes"]

    notify_route.mock(return_value=httpx.Response(200, json={"status": "ok"}))
    await sync.maybe_escalate({"id": 9, "labels": ["escalate"]})
    assert notify_route.call_count == 2
    assert state["custom_attributes"].get("escalation_notified_at")
    get_proton_config_client.cache_clear()


@respx.mock
async def test_maybe_escalate_rearms_when_the_escalate_label_is_removed(monkeypatch):
    """Removing `escalate` re-arms the guard, so a genuine later re-escalation
    of the same case still notifies (the guard is once per escalation, not
    once per conversation for all time)."""
    state = _stateful_email_conversation(monkeypatch)
    notify_route = respx.post(f"{PROTON}/escalation/notify").mock(
        return_value=httpx.Response(200, json={"status": "ok"})
    )

    await sync.maybe_escalate({"id": 9, "labels": ["escalate"]})
    assert notify_route.call_count == 1
    assert state["custom_attributes"].get("escalation_notified_at")

    # The agent removes the label; Chatwoot echoes the conversation's current
    # custom_attributes back in the `conversation_updated` payload, so the
    # re-arm costs no extra read.
    await sync.maybe_escalate(
        {"id": 9, "labels": [], "custom_attributes": dict(state["custom_attributes"])}
    )
    assert not state["custom_attributes"].get("escalation_notified_at")

    # ...and weeks later the case is escalated again.
    await sync.maybe_escalate({"id": 9, "labels": ["escalate"]})
    assert notify_route.call_count == 2
    get_proton_config_client.cache_clear()


@respx.mock
async def test_maybe_escalate_rearm_is_free_when_there_is_nothing_to_clear(monkeypatch):
    """The overwhelming majority of `conversation_updated` events carry no
    `escalate` label and no stamp. Those must make zero Chatwoot calls --
    the re-arm reads the stamp off the webhook payload, never with a GET."""
    monkeypatch.setattr(get_settings(), "email_escalation_enabled", True)
    conv_route = respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/9")
    attrs_route = respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/9/custom_attributes"
    )

    await sync.maybe_escalate({"id": 9, "labels": ["billing"]})
    await sync.maybe_escalate(
        {"id": 9, "labels": ["billing"], "custom_attributes": {"case_category": "Sales"}}
    )

    assert not conv_route.called
    assert not attrs_route.called


@respx.mock
async def test_maybe_escalate_skips_notify_when_flag_off(monkeypatch):
    """`email_escalation_enabled` defaults False -- the helper must return
    before making any Chatwoot call (byte-identical no-op when unset)."""
    monkeypatch.setattr(get_settings(), "email_escalation_enabled", False)

    conv_route = respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/9")

    await sync.maybe_escalate({"id": 9, "labels": ["escalate"]})

    assert not conv_route.called


@respx.mock
async def test_maybe_escalate_threads_the_ack_onto_the_customers_own_mail(monkeypatch):
    """Chatwoot keeps the inbound mail's RFC Message-ID on the message's
    `source_id` (verified against chatwoot_proton.messages: bare ids like
    `CAB5fbLT...@mail.gmail.com`). Passing it makes the acknowledgement land
    in the customer's thread instead of arriving as a new one."""
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
    threaded = {
        "payload": [
            {**MESSAGES_RESPONSE["payload"][0], "source_id": "CAB5fbLT@mail.gmail.com"},
            *MESSAGES_RESPONSE["payload"][1:],
        ]
    }
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/9/messages").mock(
        return_value=httpx.Response(200, json=threaded)
    )
    notify_route = respx.post(f"{PROTON}/escalation/notify").mock(
        return_value=httpx.Response(200, json={"status": "ok"})
    )
    respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/9/custom_attributes").mock(
        return_value=httpx.Response(200, json={})
    )

    await sync.maybe_escalate({"id": 9, "labels": ["escalate", "dept_apps"]})

    sent = json.loads(notify_route.calls[0].request.content)
    assert sent["customer_in_reply_to"] == "CAB5fbLT@mail.gmail.com"
    get_proton_config_client.cache_clear()


@respx.mock
async def test_a_message_without_a_source_id_simply_is_not_threaded(monkeypatch):
    """A non-email inbox has no Message-ID. That means unthreaded mail, never
    a failed escalation."""
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
    respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/9/custom_attributes").mock(
        return_value=httpx.Response(200, json={})
    )

    await sync.maybe_escalate({"id": 9, "labels": ["escalate", "dept_apps"]})

    sent = json.loads(notify_route.calls[0].request.content)
    assert sent["customer_in_reply_to"] is None
    get_proton_config_client.cache_clear()
