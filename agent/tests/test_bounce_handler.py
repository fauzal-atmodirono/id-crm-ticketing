"""§4.39, the other half — an escalation email that bounced.

The send-failure note covers SMTP refusing the handoff. It does NOT cover the
commoner case: SMTP accepts the mail, and the recipient's server rejects it
minutes later with a delivery-status notification. The PIC was never told, and
nobody knows.

This was previously reported as needing a dedicated bounce mailbox (client
question Q6). It does not. Gmail returns the DSN to the envelope sender, which
IS the tenant's Email inbox -- 23 such notices were found sitting in proton's
inbox on 2026-08-09, filed as ordinary conversations nobody had read. So the
signal is already arriving; it was only ever being ignored.

Two jobs, and the second matters as much as the first: link the bounce back to
the case that caused it, and get the DSN conversation out of the agent queue.
Left alone they accumulate as open cases and inflate the SLA backlog, which is
exactly what happened on the live tenant.
"""

import json

import httpx
import respx

from app.clients.deps import get_proton_config_client
from app.config import get_settings
from app.services import bounce_handler

CHATWOOT = "http://chatwoot-rails:3000"

# Verbatim shape of a real Gmail DSN, captured from proton conversation 89.
DSN_BODY = """** Delivery incomplete **
There was a temporary problem delivering your message to pic@emas.proton.com.
Gmail will retry for 46 more hours.
The response was:
The recipient server did not accept our requests to connect.

----- Original message -----
Subject: [Escalation] [CASE-42] my car will not start
To: pic@emas.proton.com
"""


def _payload(*, sender="mailer-daemon@googlemail.com", subject="Delivery Status Notification (Failure)", content=DSN_BODY, conv_id=90, message_type="incoming"):
    return {
        "event": "message_created",
        "id": 900,
        "message_type": message_type,
        "content": content,
        "conversation": {"id": conv_id},
        "inbox": {"id": 4},
        "sender": {"email": sender, "name": "Mail Delivery Subsystem"},
        "content_attributes": {"email": {"from": [sender], "subject": subject}},
    }


def _enable(monkeypatch, value=True):
    monkeypatch.setattr(get_settings(), "bounce_handling_enabled", value)
    get_proton_config_client.cache_clear()


def _stub():
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/inboxes/4").mock(
        return_value=httpx.Response(200, json={"id": 4, "channel_type": "Channel::Email"})
    )
    for cid in (42, 90):
        respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/{cid}").mock(
            return_value=httpx.Response(200, json={"id": cid, "custom_attributes": {}})
        )
        respx.post(
            f"{CHATWOOT}/api/v1/accounts/1/conversations/{cid}/custom_attributes"
        ).mock(return_value=httpx.Response(200, json={}))
        respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/{cid}/labels").mock(
            return_value=httpx.Response(200, json={"payload": []})
        )
        respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/{cid}/labels").mock(
            return_value=httpx.Response(200, json={})
        )
        respx.post(
            f"{CHATWOOT}/api/v1/accounts/1/conversations/{cid}/toggle_status"
        ).mock(return_value=httpx.Response(200, json={}))
    return respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )


# --- detection (pure) -----------------------------------------------------


def test_a_mailer_daemon_sender_is_a_bounce():
    assert bounce_handler.is_bounce(_payload()) is True


def test_a_delivery_status_subject_is_a_bounce_even_from_another_sender():
    assert bounce_handler.is_bounce(
        _payload(sender="postmaster@example.com", subject="Delivery Status Notification (Failure)")
    ) is True


def test_an_ordinary_customer_email_is_not_a_bounce():
    assert bounce_handler.is_bounce(
        _payload(sender="customer@test", subject="my car will not start", content="help")
    ) is False


def test_an_outgoing_message_is_never_a_bounce():
    assert bounce_handler.is_bounce(_payload(message_type="outgoing")) is False


def test_the_failed_recipient_is_extracted_from_the_body():
    assert "pic@emas.proton.com" in bounce_handler.failed_recipients(DSN_BODY)


def test_the_bounce_sender_is_not_reported_as_a_failed_recipient():
    """mailer-daemon is who told us, not who could not be reached."""
    assert not any(
        "googlemail" in address for address in bounce_handler.failed_recipients(DSN_BODY)
    )


def test_a_body_naming_nobody_yields_no_recipients():
    assert bounce_handler.failed_recipients("Delivery incomplete.") == []


# --- the handler ----------------------------------------------------------


@respx.mock
async def test_a_bounce_posts_a_private_note_on_the_original_case(monkeypatch):
    _enable(monkeypatch)
    notes = _stub()

    await bounce_handler.maybe_handle_bounce(_payload())

    assert notes.called
    body = json.loads(notes.calls.last.request.read())
    assert body["private"] is True
    assert "pic@emas.proton.com" in body["content"]


@respx.mock
async def test_the_note_never_reaches_the_customer(monkeypatch):
    """The customer must not be told our escalation mail bounced."""
    _enable(monkeypatch)
    notes = _stub()

    await bounce_handler.maybe_handle_bounce(_payload())

    assert json.loads(notes.calls.last.request.read())["private"] is True


@respx.mock
async def test_the_dsn_conversation_is_resolved_and_labelled(monkeypatch):
    """Otherwise they pile up as open cases and inflate the SLA backlog --
    which is exactly what happened on the live tenant."""
    _enable(monkeypatch)
    _stub()

    await bounce_handler.maybe_handle_bounce(_payload())

    labelled = [
        json.loads(c.request.read())
        for c in respx.calls
        if c.request.url.path.endswith("/conversations/90/labels")
        and c.request.method == "POST"
    ]
    resolved = [
        c for c in respx.calls
        if c.request.url.path.endswith("/conversations/90/toggle_status")
    ]
    assert labelled and "bounce" in labelled[0]["labels"]
    assert resolved


@respx.mock
async def test_a_bounce_with_no_case_tag_is_still_tidied_away(monkeypatch):
    """Not every bounce is ours. It still should not sit in the queue."""
    _enable(monkeypatch)
    notes = _stub()

    await bounce_handler.maybe_handle_bounce(
        _payload(content="Delivery incomplete to somebody@example.com. No tag here.")
    )

    assert not notes.called
    resolved = [
        c for c in respx.calls
        if c.request.url.path.endswith("/conversations/90/toggle_status")
    ]
    assert resolved


@respx.mock
async def test_the_case_is_stamped_so_reporting_can_see_the_bounce(monkeypatch):
    _enable(monkeypatch)
    _stub()

    await bounce_handler.maybe_handle_bounce(_payload())

    stamped = [
        json.loads(c.request.read())
        for c in respx.calls
        if c.request.url.path.endswith("/conversations/42/custom_attributes")
    ]
    assert stamped and "escalation_bounced_at" in stamped[0]["custom_attributes"]


@respx.mock
async def test_an_ordinary_message_is_untouched(monkeypatch):
    _enable(monkeypatch)
    notes = _stub()

    await bounce_handler.maybe_handle_bounce(
        _payload(sender="customer@test", subject="hello", content="my car broke")
    )

    assert not notes.called


@respx.mock
async def test_the_flag_off_does_nothing_at_all(monkeypatch):
    _enable(monkeypatch, value=False)
    notes = _stub()

    await bounce_handler.maybe_handle_bounce(_payload())

    assert not notes.called
    assert not respx.calls


@respx.mock
async def test_a_chatwoot_failure_is_swallowed(monkeypatch):
    """A background task must never raise for an expected failure."""
    _enable(monkeypatch)
    _stub()
    respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages").mock(
        return_value=httpx.Response(500)
    )

    await bounce_handler.maybe_handle_bounce(_payload())


# --- Task 32 / CONV-031: match by the quoted Message-ID, not only [CASE-n] ---

ORIG_MID = "conversation/abc-123/messages/77@proton.example"

# A DSN that quotes no [CASE-n] subject tag, only the original Message-ID.
DSN_NO_TOKEN = f"""** Delivery incomplete **
Your message to customer@example.com could not be delivered.

----- Original message -----
Message-ID: <{ORIG_MID}>
To: customer@example.com
Subject: Re: my car will not start
"""

DSN_RFC822 = f"""Content-Type: message/delivery-status

Final-Recipient: rfc822; customer@example.com
Action: failed

Content-Type: message/rfc822

From: support@proton.example
To: customer@example.com
References: <older@x> <{ORIG_MID}>
Subject: Re: my car will not start
"""


def _stub_search(*, source_id=ORIG_MID, conv=55):
    """Contact search -> that contact's conversation -> its messages."""
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/contacts/search").mock(
        return_value=httpx.Response(200, json={"payload": [{"id": 7, "email": "customer@example.com"}]})
    )
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/contacts/7/conversations").mock(
        return_value=httpx.Response(200, json={"payload": [{"id": conv}]})
    )
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/{conv}/messages").mock(
        return_value=httpx.Response(
            200,
            json={"payload": [
                {"id": 1, "message_type": 0, "source_id": "inbound@x"},
                {"id": 2, "message_type": 1, "source_id": source_id},
            ]},
        )
    )
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations").mock(
        return_value=httpx.Response(200, json={"data": {"payload": []}})
    )
    _stub_conv(conv)
    return respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/{conv}/messages").mock(
        return_value=httpx.Response(200, json={"id": 9})
    )


def _stub_conv(cid):
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/{cid}").mock(
        return_value=httpx.Response(200, json={"id": cid, "custom_attributes": {}})
    )
    respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/{cid}/custom_attributes").mock(
        return_value=httpx.Response(200, json={})
    )
    for suffix in ("labels", "toggle_status"):
        respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/{cid}/{suffix}").mock(
            return_value=httpx.Response(200, json={})
        )
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/{cid}/labels").mock(
        return_value=httpx.Response(200, json={"payload": []})
    )


def test_message_ids_extracted_from_headers_and_rfc822_part():
    assert bounce_handler.extract_message_ids(DSN_NO_TOKEN) == [ORIG_MID]
    ids = bounce_handler.extract_message_ids(DSN_RFC822)
    assert ORIG_MID in ids and "older@x" in ids


@respx.mock
async def test_bounce_matched_by_in_reply_to(monkeypatch):
    """Chatwoot's DSN conversation carries the original Message-ID as the
    email In-Reply-To; that is enough to find the case."""
    _enable(monkeypatch)
    _stub_conv(90)
    notes = _stub_search()
    payload = _payload(content="Delivery incomplete to customer@example.com.")
    payload["content_attributes"]["email"]["in_reply_to"] = f"<{ORIG_MID}>"

    await bounce_handler.maybe_handle_bounce(payload)

    assert notes.called
    body = json.loads(notes.calls.last.request.read())
    assert body["private"] is True and "customer@example.com" in body["content"]


@respx.mock
async def test_bounce_without_case_token_posts_note(monkeypatch):
    _enable(monkeypatch)
    _stub_conv(90)
    notes = _stub_search()

    await bounce_handler.maybe_handle_bounce(_payload(content=DSN_NO_TOKEN))

    assert notes.called
    assert json.loads(notes.calls.last.request.read())["private"] is True
    stamped = [
        c for c in respx.calls
        if c.request.url.path.endswith("/conversations/55/custom_attributes")
    ]
    assert stamped


@respx.mock
async def test_bounce_rfc822_part_parsed(monkeypatch):
    _enable(monkeypatch)
    _stub_conv(90)
    notes = _stub_search()

    await bounce_handler.maybe_handle_bounce(_payload(content=DSN_RFC822))

    assert notes.called


@respx.mock
async def test_unrelated_bounce_ignored(monkeypatch):
    """A Message-ID that no conversation sent: no note anywhere, DSN still
    tidied."""
    _enable(monkeypatch)
    _stub_conv(90)
    notes = _stub_search(source_id="something-else@x")

    await bounce_handler.maybe_handle_bounce(_payload(content=DSN_NO_TOKEN))

    assert not notes.called
    assert any(
        c.request.url.path.endswith("/conversations/90/toggle_status") for c in respx.calls
    )


@respx.mock
async def test_case_token_still_wins_over_message_id(monkeypatch):
    _enable(monkeypatch)
    notes = _stub()  # [CASE-42] path
    search = respx.get(f"{CHATWOOT}/api/v1/accounts/1/contacts/search")
    await bounce_handler.maybe_handle_bounce(_payload(content=DSN_BODY + f"\nMessage-ID: <{ORIG_MID}>"))
    assert notes.called and not search.called
