"""Tests for `duplicate_detection` (FIX-46, RFP 4.25): link a new case to the
customer's other OPEN cases by contact, mobile number, email, vehicle
registration or chassis number, and tell both sides -- never merge anything,
never message the customer.
"""

import json
from unittest.mock import AsyncMock

import httpx
import respx

from app.config import get_settings
from app.services import duplicate_detection

CHATWOOT = "http://chatwoot-rails:3000"
API = f"{CHATWOOT}/api/v1/accounts/1"

NEW_ID = 100


def _enable(monkeypatch, **extra):
    monkeypatch.setattr(get_settings(), "duplicate_detection_enabled", True)
    monkeypatch.setattr(get_settings(), "duplicate_detection_inbox_ids", "")
    monkeypatch.setattr(get_settings(), "duplicate_detection_ai_judge_enabled", False)
    for key, value in extra.items():
        monkeypatch.setattr(get_settings(), key, value)


def _created(contact=None, conv_attrs=None, inbox_id=3, conv_id=NEW_ID, event="conversation_created"):
    return {
        "event": event,
        "id": conv_id,
        "inbox_id": inbox_id,
        "status": "open",
        "custom_attributes": conv_attrs or {},
        "additional_attributes": {},
        "messages": [],
        "meta": {
            "sender": contact
            or {"id": 55, "name": "Jane", "phone_number": "+60123456789", "custom_attributes": {}},
        },
    }


def _conv(conv_id, status="open", inbox_id=3, assignee=None, team=None, attrs=None):
    return {
        "id": conv_id,
        "status": status,
        "inbox_id": inbox_id,
        "custom_attributes": attrs or {},
        "meta": {"assignee": assignee, "team": team},
    }


def _mock_new_conversation(conv_attrs=None):
    """Routes for writing to the new conversation. Returns the message route."""
    respx.get(f"{API}/conversations/{NEW_ID}").mock(
        return_value=httpx.Response(
            200, json={"id": NEW_ID, "inbox_id": 3, "custom_attributes": conv_attrs or {}}
        )
    )
    respx.get(f"{API}/conversations/{NEW_ID}/labels").mock(
        return_value=httpx.Response(200, json={"payload": ["sales"]})
    )
    labels = respx.post(f"{API}/conversations/{NEW_ID}/labels").mock(
        return_value=httpx.Response(200, json={})
    )
    attrs = respx.post(f"{API}/conversations/{NEW_ID}/custom_attributes").mock(
        return_value=httpx.Response(200, json={})
    )
    note = respx.post(f"{API}/conversations/{NEW_ID}/messages").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    return note, labels, attrs


def _mock_contact_conversations(contact_id, conversations):
    respx.get(f"{API}/contacts/{contact_id}/conversations").mock(
        return_value=httpx.Response(200, json={"payload": conversations})
    )


def _mock_search(q, contacts):
    respx.get(f"{API}/contacts/search", params={"q": q}).mock(
        return_value=httpx.Response(200, json={"payload": contacts})
    )


def _body(route, index=-1):
    return json.loads(route.calls[index].request.content)


@respx.mock
async def test_same_phone_open_case_suggests_merge(monkeypatch):
    _enable(monkeypatch)
    _mock_contact_conversations(55, [_conv(NEW_ID)])
    # Same number typed differently on another (e.g. email-channel) contact.
    _mock_search("+60123456789", [{"id": 77, "phone_number": "0123456789"}])
    _mock_contact_conversations(77, [_conv(12)])
    existing_note = respx.post(f"{API}/conversations/12/messages").mock(
        return_value=httpx.Response(200, json={"id": 2})
    )
    note, labels, attrs = _mock_new_conversation()

    await duplicate_detection.maybe_detect_duplicates(_created())

    assert note.call_count == 1
    body = _body(note)
    assert body["private"] is True
    assert "#12" in body["content"]
    assert "mobile number" in body["content"]
    assert "possible_duplicate" in _body(labels)["labels"]
    assert "sales" in _body(labels)["labels"]  # existing labels kept
    assert _body(attrs, 0)["custom_attributes"]["related_case_ids"] == "12"
    assert existing_note.call_count == 1
    assert _body(existing_note)["private"] is True


@respx.mock
async def test_resolved_case_not_matched(monkeypatch):
    _enable(monkeypatch)
    _mock_contact_conversations(55, [_conv(NEW_ID), _conv(11, status="resolved")])
    _mock_search("+60123456789", [{"id": 77, "phone_number": "0123456789"}])
    _mock_contact_conversations(77, [_conv(12, status="resolved")])
    note, labels, attrs = _mock_new_conversation()

    await duplicate_detection.maybe_detect_duplicates(_created())

    assert not note.called
    assert not labels.called
    assert not attrs.called


@respx.mock
async def test_merge_notifies_assigned_pic(monkeypatch):
    _enable(monkeypatch)
    _mock_contact_conversations(
        55, [_conv(NEW_ID), _conv(12, assignee={"id": 9, "name": "Aina PIC"})]
    )
    _mock_search("+60123456789", [{"id": 55, "phone_number": "+60123456789"}])
    existing_note = respx.post(f"{API}/conversations/12/messages").mock(
        return_value=httpx.Response(200, json={"id": 2})
    )
    note, _, _ = _mock_new_conversation()

    await duplicate_detection.maybe_detect_duplicates(_created())

    assert existing_note.call_count == 1
    body = _body(existing_note)
    assert body["private"] is True
    # A Chatwoot mention in a private note is what raises the assignee's
    # in-app notification.
    assert "mention://user/9/" in body["content"]
    assert f"#{NEW_ID}" in body["content"]
    # The new case's note names who already owns the open one.
    assert "Aina PIC" in _body(note)["content"]
    assert "same customer" in _body(note)["content"]


@respx.mock
async def test_same_plate_different_contact_matches(monkeypatch):
    _enable(monkeypatch)
    contact = {
        "id": 55,
        "name": "Jane",
        "phone_number": None,
        "custom_attributes": {"vehicle_no": "bmt 5518"},
    }
    _mock_contact_conversations(55, [_conv(NEW_ID)])
    contacts_filter = respx.post(f"{API}/contacts/filter").mock(
        return_value=httpx.Response(
            200,
            json={
                "payload": [
                    {"id": 66, "custom_attributes": {"vehicle_no": "BMT55189"}},  # neighbour
                    {"id": 88, "custom_attributes": {"vehicle_no": "BMT-5518"}},
                ]
            },
        )
    )
    respx.post(f"{API}/conversations/filter").mock(
        return_value=httpx.Response(200, json={"payload": []})
    )
    _mock_contact_conversations(66, [_conv(29)])
    _mock_contact_conversations(88, [_conv(30)])
    respx.post(f"{API}/conversations/30/messages").mock(
        return_value=httpx.Response(200, json={"id": 2})
    )
    note, _, attrs = _mock_new_conversation()

    await duplicate_detection.maybe_detect_duplicates(_created(contact=contact))

    assert contacts_filter.called
    content = _body(note)["content"]
    assert "#30" in content
    assert "#29" not in content
    assert "vehicle registration" in content
    assert _body(attrs, 0)["custom_attributes"]["related_case_ids"] == "30"


@respx.mock
async def test_idempotent_on_redelivery(monkeypatch):
    _enable(monkeypatch)
    _mock_contact_conversations(55, [_conv(NEW_ID), _conv(12)])
    _mock_search("+60123456789", [])
    existing_note = respx.post(f"{API}/conversations/12/messages").mock(
        return_value=httpx.Response(200, json={"id": 2})
    )
    note, labels, _ = _mock_new_conversation()

    await duplicate_detection.maybe_detect_duplicates(_created())
    await duplicate_detection.maybe_detect_duplicates(_created())

    assert note.call_count == 1
    assert labels.call_count == 1
    assert existing_note.call_count == 1


@respx.mock
async def test_already_flagged_conversation_is_left_alone(monkeypatch):
    """The conversation attribute is the cross-process marker: a conversation
    that already carries related_case_ids is never noted twice."""
    _enable(monkeypatch)
    _mock_contact_conversations(55, [_conv(NEW_ID), _conv(12)])
    _mock_search("+60123456789", [])
    note, labels, _ = _mock_new_conversation(conv_attrs={"related_case_ids": "12", "duplicate_note_posted": "true"})

    await duplicate_detection.maybe_detect_duplicates(_created())

    assert not note.called
    assert not labels.called


@respx.mock(assert_all_called=False)
async def test_flag_off_noop(monkeypatch):
    monkeypatch.setattr(get_settings(), "duplicate_detection_enabled", False)

    await duplicate_detection.maybe_detect_duplicates(_created())
    await duplicate_detection.maybe_detect_duplicates_on_update(
        _created(conv_attrs={"vehicle_chassis": "PM2ABC123"}, event="conversation_updated")
    )

    assert respx.calls.call_count == 0


@respx.mock(assert_all_called=False)
async def test_inbox_not_in_allow_list_noop(monkeypatch):
    _enable(monkeypatch, duplicate_detection_inbox_ids="5, 7")

    await duplicate_detection.maybe_detect_duplicates(_created(inbox_id=3))

    assert respx.calls.call_count == 0


@respx.mock
async def test_case_in_inbox_outside_allow_list_is_not_touched(monkeypatch):
    _enable(monkeypatch, duplicate_detection_inbox_ids="3")
    _mock_contact_conversations(55, [_conv(NEW_ID), _conv(12, inbox_id=9)])
    _mock_search("+60123456789", [])
    note, _, _ = _mock_new_conversation()

    await duplicate_detection.maybe_detect_duplicates(_created())

    assert not note.called


@respx.mock
async def test_downstream_failure_never_raises(monkeypatch):
    _enable(monkeypatch)
    respx.get(f"{API}/contacts/55/conversations").mock(return_value=httpx.Response(500))
    respx.get(f"{API}/contacts/search").mock(side_effect=httpx.ConnectError("down"))

    await duplicate_detection.maybe_detect_duplicates(_created())  # no exception


@respx.mock
async def test_chassis_set_later_on_update_matches(monkeypatch):
    """The chassis number is a case field an agent fills in after the case
    exists, so conversation_updated re-checks once it appears."""
    _enable(monkeypatch)
    payload = _created(
        contact={"id": 55, "name": "Jane", "custom_attributes": {}},
        conv_attrs={"vehicle_chassis": "pm2-abc123"},
        event="conversation_updated",
    )
    _mock_contact_conversations(55, [_conv(NEW_ID)])
    conv_filter = respx.post(f"{API}/conversations/filter").mock(
        return_value=httpx.Response(
            200,
            json={
                "payload": [
                    _conv(NEW_ID, attrs={"vehicle_chassis": "PM2ABC123"}),
                    _conv(41, attrs={"vehicle_chassis": "PM2ABC123"}),
                ]
            },
        )
    )
    respx.post(f"{API}/conversations/41/messages").mock(
        return_value=httpx.Response(200, json={"id": 2})
    )
    note, _, _ = _mock_new_conversation()

    await duplicate_detection.maybe_detect_duplicates_on_update(payload)

    assert conv_filter.called
    filter_body = _body(conv_filter)
    assert filter_body["payload"][0]["attribute_key"] == "vehicle_chassis"
    assert filter_body["payload"][0]["values"] == ["PM2ABC123"]
    content = _body(note)["content"]
    assert "#41" in content
    assert "chassis" in content


@respx.mock(assert_all_called=False)
async def test_update_without_vehicle_fields_does_nothing(monkeypatch):
    _enable(monkeypatch)

    await duplicate_detection.maybe_detect_duplicates_on_update(
        _created(event="conversation_updated")
    )

    assert respx.calls.call_count == 0


@respx.mock
async def test_ai_judge_drops_a_different_issue_from_the_same_customer(monkeypatch):
    _enable(monkeypatch, duplicate_detection_ai_judge_enabled=True)
    _mock_contact_conversations(55, [_conv(NEW_ID), _conv(12)])
    _mock_search("+60123456789", [])
    msg = lambda text: {"payload": [{"content": text, "private": False, "message_type": 0}]}  # noqa: E731
    respx.get(f"{API}/conversations/{NEW_ID}/messages").mock(
        return_value=httpx.Response(200, json=msg("When is the e.MAS 7 launch?"))
    )
    respx.get(f"{API}/conversations/12/messages").mock(
        return_value=httpx.Response(200, json=msg("My aircond is broken"))
    )
    judge = AsyncMock(return_value="NO")
    monkeypatch.setattr(duplicate_detection.gemini, "generate", judge)
    note, _, _ = _mock_new_conversation()

    await duplicate_detection.maybe_detect_duplicates(_created())

    assert judge.await_count == 1
    assert not note.called


@respx.mock
async def test_ai_judge_error_keeps_the_deterministic_match(monkeypatch):
    _enable(monkeypatch, duplicate_detection_ai_judge_enabled=True)
    _mock_contact_conversations(55, [_conv(NEW_ID), _conv(12)])
    _mock_search("+60123456789", [])
    msg = {"payload": [{"content": "aircond broken", "private": False, "message_type": 0}]}
    respx.get(f"{API}/conversations/{NEW_ID}/messages").mock(
        return_value=httpx.Response(200, json=msg)
    )
    respx.get(f"{API}/conversations/12/messages").mock(return_value=httpx.Response(200, json=msg))
    respx.post(f"{API}/conversations/12/messages").mock(
        return_value=httpx.Response(200, json={"id": 2})
    )
    monkeypatch.setattr(
        duplicate_detection.gemini, "generate", AsyncMock(side_effect=RuntimeError("quota"))
    )
    note, _, _ = _mock_new_conversation()

    await duplicate_detection.maybe_detect_duplicates(_created())

    assert note.call_count == 1


async def test_router_dispatches_created_and_updated(monkeypatch):
    import hashlib
    import hmac
    import time

    from httpx import ASGITransport, AsyncClient

    from app.main import create_app
    from app.routers import chatwoot as router_module

    monkeypatch.setattr(get_settings(), "duplicate_detection_enabled", True)
    created = AsyncMock()
    updated = AsyncMock()
    monkeypatch.setattr(router_module.duplicate_detection, "maybe_detect_duplicates", created)
    monkeypatch.setattr(
        router_module.duplicate_detection, "maybe_detect_duplicates_on_update", updated
    )
    contact = AsyncMock()
    monkeypatch.setattr(
        router_module.duplicate_detection, "maybe_detect_duplicates_on_contact_update", contact
    )
    for name in ("maybe_escalate", "maybe_stamp_dealer_escalation", "maybe_validate_follow_up_date"):
        monkeypatch.setattr(router_module.sync, name, AsyncMock())

    async def post(client, payload, delivery):
        body = json.dumps(payload).encode()
        ts = str(int(time.time()))
        mac = hmac.new(b"test-chatwoot-webhook-secret", f"{ts}.".encode() + body, hashlib.sha256)
        headers = {
            "X-Chatwoot-Signature": f"sha256={mac.hexdigest()}",
            "X-Chatwoot-Timestamp": ts,
            "X-Chatwoot-Delivery": delivery,
            "Content-Type": "application/json",
        }
        return await client.post("/webhooks/chatwoot", content=body, headers=headers)

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await post(client, _created(), "dup-d1")).status_code == 200
        assert (
            await post(client, _created(event="conversation_updated"), "dup-d2")
        ).status_code == 200
        contact_event = {"event": "contact_updated", "id": 55, "custom_attributes": {}}
        assert (await post(client, contact_event, "dup-d3")).status_code == 200

    assert created.await_count == 1
    assert updated.await_count == 1
    assert contact.await_count == 1


# --- review fixes: marker-first, bounded updates, contact_updated ---------


@respx.mock
async def test_marker_written_before_label_so_label_update_does_not_renote(monkeypatch):
    """The label write makes Chatwoot emit conversation_updated. Its payload
    must already carry related_case_ids, so it cannot re-enter and post a
    second note -- even when it also carries a vehicle field."""
    _enable(monkeypatch)
    _mock_contact_conversations(55, [_conv(NEW_ID), _conv(12)])
    _mock_search("+60123456789", [])
    existing_note = respx.post(f"{API}/conversations/12/messages").mock(
        return_value=httpx.Response(200, json={"id": 2})
    )
    respx.get(f"{API}/conversations/{NEW_ID}").mock(
        return_value=httpx.Response(200, json={"id": NEW_ID, "custom_attributes": {}})
    )
    respx.get(f"{API}/conversations/{NEW_ID}/labels").mock(
        return_value=httpx.Response(200, json={"payload": []})
    )
    state: dict = {}
    snapshots: list[dict] = []

    def write_attrs(request):
        state.update(json.loads(request.content)["custom_attributes"])
        return httpx.Response(200, json={})

    def write_labels(request):
        snapshots.append(dict(state))  # what the label's conversation_updated carries
        return httpx.Response(200, json={})

    respx.post(f"{API}/conversations/{NEW_ID}/custom_attributes").mock(side_effect=write_attrs)
    respx.post(f"{API}/conversations/{NEW_ID}/labels").mock(side_effect=write_labels)
    note = respx.post(f"{API}/conversations/{NEW_ID}/messages").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    conv_filter = respx.post(f"{API}/conversations/filter").mock(
        return_value=httpx.Response(200, json={"payload": [_conv(41)]})
    )

    await duplicate_detection.maybe_detect_duplicates(_created())
    assert snapshots and snapshots[0].get("related_case_ids") == "12"

    label_update = _created(
        conv_attrs={**snapshots[0], "vehicle_chassis": "PM2ABC123"}, event="conversation_updated"
    )
    await duplicate_detection.maybe_detect_duplicates_on_update(label_update)

    assert note.call_count == 1
    assert existing_note.call_count == 1
    assert not conv_filter.called


@respx.mock
async def test_repeated_updates_with_same_signals_search_once(monkeypatch):
    _enable(monkeypatch)
    payload = _created(
        contact={"id": 55, "name": "Jane", "custom_attributes": {}},
        conv_attrs={"vehicle_chassis": "PM2ABC123"},
        event="conversation_updated",
    )
    contact_convs = respx.get(f"{API}/contacts/55/conversations").mock(
        return_value=httpx.Response(200, json={"payload": [_conv(NEW_ID)]})
    )
    conv_filter = respx.post(f"{API}/conversations/filter").mock(
        return_value=httpx.Response(200, json={"payload": []})
    )

    await duplicate_detection.maybe_detect_duplicates_on_update(payload)
    # a status / assignment / label change: same vehicle signals
    await duplicate_detection.maybe_detect_duplicates_on_update({**payload, "status": "pending"})

    assert conv_filter.call_count == 1
    assert contact_convs.call_count == 1

    # a changed chassis is a new signal and is searched once more
    changed = _created(
        contact={"id": 55, "name": "Jane", "custom_attributes": {}},
        conv_attrs={"vehicle_chassis": "PM2XYZ999"},
        event="conversation_updated",
    )
    await duplicate_detection.maybe_detect_duplicates_on_update(changed)
    assert conv_filter.call_count == 2


@respx.mock
async def test_contact_vehicle_no_added_later_links_open_case(monkeypatch):
    _enable(monkeypatch)
    event = {
        "event": "contact_updated",
        "id": 55,
        "name": "Jane",
        "phone_number": None,
        "custom_attributes": {"vehicle_no": "BMT 5518"},
    }
    _mock_contact_conversations(55, [_conv(NEW_ID), _conv(7, status="resolved")])
    contacts_filter = respx.post(f"{API}/contacts/filter").mock(
        return_value=httpx.Response(
            200, json={"payload": [{"id": 88, "custom_attributes": {"vehicle_no": "BMT5518"}}]}
        )
    )
    respx.post(f"{API}/conversations/filter").mock(
        return_value=httpx.Response(200, json={"payload": []})
    )
    _mock_contact_conversations(88, [_conv(30)])
    existing_note = respx.post(f"{API}/conversations/30/messages").mock(
        return_value=httpx.Response(200, json={"id": 2})
    )
    note, _, attrs = _mock_new_conversation()

    await duplicate_detection.maybe_detect_duplicates_on_contact_update(event)
    await duplicate_detection.maybe_detect_duplicates_on_contact_update(event)  # unchanged

    assert note.call_count == 1
    assert "#30" in _body(note)["content"]
    assert _body(attrs, 0)["custom_attributes"]["related_case_ids"] == "30"
    assert existing_note.call_count == 1
    assert contacts_filter.call_count == 1


@respx.mock(assert_all_called=False)
async def test_contact_update_without_vehicle_fields_does_nothing(monkeypatch):
    _enable(monkeypatch)

    await duplicate_detection.maybe_detect_duplicates_on_contact_update(
        {"event": "contact_updated", "id": 55, "custom_attributes": {"city": "KL"}}
    )

    assert respx.calls.call_count == 0


@respx.mock
async def test_note_failure_releases_the_claim_for_a_retry(monkeypatch):
    _enable(monkeypatch)
    _mock_contact_conversations(55, [_conv(NEW_ID), _conv(12)])
    _mock_search("+60123456789", [])
    respx.post(f"{API}/conversations/12/messages").mock(
        return_value=httpx.Response(200, json={"id": 2})
    )
    note, labels, attrs = _mock_new_conversation()
    note.side_effect = [httpx.Response(500), httpx.Response(200, json={"id": 1})]

    await duplicate_detection.maybe_detect_duplicates(_created())
    assert not labels.called
    # marker rolled back so the retry is not blocked by it
    assert _body(attrs)["custom_attributes"]["related_case_ids"] == ""

    await duplicate_detection.maybe_detect_duplicates(_created())

    assert note.call_count == 2
    assert labels.call_count == 1
    assert _body(attrs, 2)["custom_attributes"]["related_case_ids"] == "12"
    assert _body(attrs)["custom_attributes"]["duplicate_note_posted"] == "true"


@respx.mock
async def test_bot_assignee_is_not_mentioned_team_is(monkeypatch):
    _enable(monkeypatch)
    bot_held = _conv(12, assignee={"id": 3, "name": "Proton Bot"}, team={"id": 2, "name": "Aftersales"})
    bot_held["meta"]["assignee_type"] = "AgentBot"
    _mock_contact_conversations(55, [_conv(NEW_ID), bot_held])
    _mock_search("+60123456789", [])
    existing_note = respx.post(f"{API}/conversations/12/messages").mock(
        return_value=httpx.Response(200, json={"id": 2})
    )
    _mock_new_conversation()

    await duplicate_detection.maybe_detect_duplicates(_created())

    content = _body(existing_note)["content"]
    assert "mention://user/" not in content
    assert "mention://team/2/Aftersales" in content


@respx.mock
async def test_assignee_outside_new_inbox_is_logged(monkeypatch, caplog):
    _enable(monkeypatch)
    _mock_contact_conversations(
        55, [_conv(NEW_ID), _conv(12, inbox_id=5, assignee={"id": 9, "name": "Aina PIC"})]
    )
    _mock_search("+60123456789", [])
    respx.get(f"{API}/inbox_members/3").mock(
        return_value=httpx.Response(200, json={"payload": [{"id": 4}]})
    )
    existing_note = respx.post(f"{API}/conversations/12/messages").mock(
        return_value=httpx.Response(200, json={"id": 2})
    )
    _mock_new_conversation()

    with caplog.at_level("INFO", logger="app.services.duplicate_detection"):
        await duplicate_detection.maybe_detect_duplicates(_created())

    assert existing_note.call_count == 1  # still notified
    assert "not a member of inbox 3" in caplog.text


# --- review round 2: outer claims released on failure; marker half state ---


@respx.mock
async def test_update_path_failed_lookup_releases_claim_and_retries(monkeypatch):
    _enable(monkeypatch)
    payload = _created(
        contact={"id": 55, "name": "Jane", "custom_attributes": {}},
        conv_attrs={"vehicle_chassis": "PM2ABC123"},
        event="conversation_updated",
    )
    _mock_contact_conversations(55, [_conv(NEW_ID)])
    conv_filter = respx.post(f"{API}/conversations/filter").mock(
        side_effect=[
            httpx.Response(500),
            httpx.Response(
                200, json={"payload": [_conv(41, attrs={"vehicle_chassis": "PM2ABC123"})]}
            ),
        ]
    )
    respx.post(f"{API}/conversations/41/messages").mock(
        return_value=httpx.Response(200, json={"id": 2})
    )
    note, _, _ = _mock_new_conversation()

    await duplicate_detection.maybe_detect_duplicates_on_update(payload)
    assert not note.called
    await duplicate_detection.maybe_detect_duplicates_on_update(payload)  # same signals

    assert conv_filter.call_count == 2
    assert note.call_count == 1


@respx.mock
async def test_update_path_note_failure_releases_outer_claim(monkeypatch):
    _enable(monkeypatch)
    payload = _created(
        contact={"id": 55, "name": "Jane", "custom_attributes": {}},
        conv_attrs={"vehicle_chassis": "PM2ABC123"},
        event="conversation_updated",
    )
    _mock_contact_conversations(55, [_conv(NEW_ID)])
    respx.post(f"{API}/conversations/filter").mock(
        return_value=httpx.Response(
            200, json={"payload": [_conv(41, attrs={"vehicle_chassis": "PM2ABC123"})]}
        )
    )
    respx.post(f"{API}/conversations/41/messages").mock(
        return_value=httpx.Response(200, json={"id": 2})
    )
    note, _, _ = _mock_new_conversation()
    note.side_effect = [httpx.Response(500), httpx.Response(200, json={"id": 1})]

    await duplicate_detection.maybe_detect_duplicates_on_update(payload)
    await duplicate_detection.maybe_detect_duplicates_on_update(payload)

    assert note.call_count == 2  # failed once, retried once


@respx.mock
async def test_contact_path_lookup_exception_releases_claim_and_retries(monkeypatch):
    _enable(monkeypatch)
    event = {"event": "contact_updated", "id": 55, "custom_attributes": {"vehicle_no": "BMT5518"}}
    contact_convs = respx.get(f"{API}/contacts/55/conversations").mock(
        side_effect=[
            httpx.Response(500),  # the contact path's own lookup raises
            httpx.Response(200, json={"payload": [_conv(NEW_ID)]}),
            httpx.Response(200, json={"payload": [_conv(NEW_ID)]}),
        ]
    )
    respx.post(f"{API}/contacts/filter").mock(
        return_value=httpx.Response(
            200, json={"payload": [{"id": 88, "custom_attributes": {"vehicle_no": "BMT5518"}}]}
        )
    )
    respx.post(f"{API}/conversations/filter").mock(
        return_value=httpx.Response(200, json={"payload": []})
    )
    _mock_contact_conversations(88, [_conv(30)])
    respx.post(f"{API}/conversations/30/messages").mock(
        return_value=httpx.Response(200, json={"id": 2})
    )
    note, _, _ = _mock_new_conversation()

    await duplicate_detection.maybe_detect_duplicates_on_contact_update(event)
    assert not note.called
    await duplicate_detection.maybe_detect_duplicates_on_contact_update(event)

    assert contact_convs.call_count == 3
    assert note.call_count == 1


async def test_contact_path_failed_case_releases_claim(monkeypatch):
    _enable(monkeypatch)
    event = {"event": "contact_updated", "id": 55, "custom_attributes": {"vehicle_no": "BMT5518"}}
    detect = AsyncMock(side_effect=[duplicate_detection.FAILED, duplicate_detection.NO_MATCH])
    monkeypatch.setattr(duplicate_detection, "_detect", detect)

    class _Client:
        async def list_contact_conversations(self, contact_id):
            return [_conv(NEW_ID)]

    monkeypatch.setattr(duplicate_detection, "get_chatwoot_client", lambda: _Client())

    await duplicate_detection.maybe_detect_duplicates_on_contact_update(event)  # FAILED
    await duplicate_detection.maybe_detect_duplicates_on_contact_update(event)  # retried
    await duplicate_detection.maybe_detect_duplicates_on_contact_update(event)  # NO_MATCH kept

    assert detect.await_count == 2


async def test_contact_path_orders_by_recent_activity_and_logs_cap(monkeypatch, caplog):
    _enable(monkeypatch)
    event = {"event": "contact_updated", "id": 55, "custom_attributes": {"vehicle_no": "BMT5518"}}
    detect = AsyncMock(return_value=duplicate_detection.NO_MATCH)
    monkeypatch.setattr(duplicate_detection, "_detect", detect)
    convs = [{**_conv(i), "last_activity_at": 1_700_000_000 + i * 10} for i in range(1, 8)]
    convs.append({**_conv(99, attrs={"related_case_ids": "1", "duplicate_note_posted": "true"}),
                  "last_activity_at": 1_800_000_000})  # fully flagged: excluded

    class _Client:
        async def list_contact_conversations(self, contact_id):
            return convs

    monkeypatch.setattr(duplicate_detection, "get_chatwoot_client", lambda: _Client())

    with caplog.at_level("INFO", logger="app.services.duplicate_detection"):
        await duplicate_detection.maybe_detect_duplicates_on_contact_update(event)

    assert [c.args[0]["id"] for c in detect.await_args_list] == [7, 6, 5, 4, 3]
    assert "has 7 open cases" in caplog.text


@respx.mock
async def test_marker_write_timeout_after_commit_is_rolled_back(monkeypatch):
    _enable(monkeypatch)
    _mock_contact_conversations(55, [_conv(NEW_ID), _conv(12)])
    _mock_search("+60123456789", [])
    note, labels, attrs = _mock_new_conversation()
    committed: list[dict] = []

    def write(request):
        committed.append(json.loads(request.content)["custom_attributes"])
        if len(committed) == 1:
            raise httpx.ReadTimeout("chatwoot committed, then the response was lost")
        return httpx.Response(200, json={})

    attrs.side_effect = write

    await duplicate_detection.maybe_detect_duplicates(_created())

    assert committed[0]["related_case_ids"] == "12"
    assert committed[1]["related_case_ids"] == ""  # rollback attempted
    assert not note.called
    assert not labels.called


@respx.mock
async def test_stranded_marker_without_note_is_retried(monkeypatch):
    """A marker with no note flag (and no note in the thread) is a half state
    from an earlier failed run. It must not block the case forever."""
    _enable(monkeypatch)
    _mock_contact_conversations(55, [_conv(NEW_ID), _conv(12)])
    _mock_search("+60123456789", [])
    respx.post(f"{API}/conversations/12/messages").mock(
        return_value=httpx.Response(200, json={"id": 2})
    )
    respx.get(f"{API}/conversations/{NEW_ID}/messages").mock(
        return_value=httpx.Response(200, json={"payload": [{"content": "hi", "private": False}]})
    )
    note, labels, attrs = _mock_new_conversation(conv_attrs={"related_case_ids": "12"})

    await duplicate_detection.maybe_detect_duplicates(_created())

    assert note.call_count == 1
    assert labels.call_count == 1
    assert _body(attrs)["custom_attributes"]["duplicate_note_posted"] == "true"


@respx.mock
async def test_marker_with_landed_note_but_lost_flag_is_completed_not_renoted(monkeypatch):
    _enable(monkeypatch)
    _mock_contact_conversations(55, [_conv(NEW_ID), _conv(12)])
    _mock_search("+60123456789", [])
    respx.get(f"{API}/conversations/{NEW_ID}/messages").mock(
        return_value=httpx.Response(
            200,
            json={"payload": [{"content": "**Possible duplicate case.** ...", "private": True}]},
        )
    )
    note, labels, attrs = _mock_new_conversation(conv_attrs={"related_case_ids": "12"})

    await duplicate_detection.maybe_detect_duplicates(_created())

    assert not note.called
    assert attrs.call_count == 1
    assert _body(attrs)["custom_attributes"] == {
        "related_case_ids": "12",
        "duplicate_note_posted": "true",
    }
