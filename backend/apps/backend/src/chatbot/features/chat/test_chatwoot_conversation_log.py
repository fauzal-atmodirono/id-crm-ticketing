from __future__ import annotations

import asyncio
from typing import Any

import pytest
from structlog.testing import capture_logs

import chatbot.features.chat.adapters.chatwoot as chatwoot_module
from chatbot.features.chat.adapters.chatwoot import ChatwootAdapter
from chatbot.features.chat.ports import ConversationLogResult
from chatbot.platform.config import Settings


class _FakeClient:
    def __init__(self, responses: dict[tuple[str, str], dict[str, Any] | None]) -> None:
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self._responses = responses

    async def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        self.calls.append((method, path, payload))
        for (m, sub), resp in self._responses.items():
            if m == method and sub in path:
                return resp
        return {}


def _adapter(fake: _FakeClient) -> ChatwootAdapter:
    a = ChatwootAdapter(Settings(chatwoot_account_id=1, chatwoot_inbox_id=7))
    a._request = fake._request  # type: ignore[method-assign]
    return a


@pytest.mark.asyncio
async def test_ensure_conversation_ticket_returns_conversation_id() -> None:
    fake = _FakeClient({("POST", "/conversations"): {"id": 55}})
    tid = await _adapter(fake).ensure_conversation_ticket(
        session_id="web-1", subject="s", customer_name=None, customer_phone=None
    )
    assert tid == "55"


@pytest.mark.asyncio
async def test_append_comment_ok() -> None:
    fake = _FakeClient({("POST", "/messages"): {"id": 1}})
    result = await _adapter(fake).append_conversation_comment("55", "note")
    assert result == ConversationLogResult.OK
    _, _path, payload = fake.calls[-1]
    assert payload is not None and payload.get("private") is True


@pytest.mark.asyncio
async def test_add_ticket_tag_posts_label() -> None:
    fake = _FakeClient({})
    await _adapter(fake).add_ticket_tag("55", "vip")
    assert fake.calls[0][0] == "GET" and "/labels" in fake.calls[0][1]
    method, path, payload = fake.calls[-1]
    assert method == "POST" and "/labels" in path and payload == {"labels": ["vip"]}


@pytest.mark.asyncio
async def test_add_ticket_tag_unions_with_existing_labels() -> None:
    """Chatwoot's labels endpoint REPLACES the whole set -- add_ticket_tag
    must GET the current labels first and POST the union, or every other
    label on the conversation (PIC, escalation, dealer, category...) is
    silently deleted the moment a second tag is ever added."""
    fake = _FakeClient({("GET", "/labels"): {"payload": ["pic_sales", "escalate"]}})
    await _adapter(fake).add_ticket_tag("55", "vip")
    method, path, payload = fake.calls[-1]
    assert method == "POST" and "/labels" in path
    assert payload == {"labels": ["pic_sales", "escalate", "vip"]}


@pytest.mark.asyncio
async def test_add_ticket_tag_skips_write_when_read_fails() -> None:
    """If the GET fails we cannot know the existing set, so adding the new
    tag to an empty list would wipe everything else. Skip the write
    entirely rather than post a set we can't prove is complete."""
    fake = _FakeClient({("GET", "/labels"): None})
    await _adapter(fake).add_ticket_tag("55", "vip")
    assert len(fake.calls) == 1  # the GET only -- no POST followed it
    assert fake.calls[0][0] == "GET"


class _StatefulLabelsClient:
    """Simulates Chatwoot's REAL persistence for the labels endpoint (a GET
    reflects whatever the most recent POST wrote). `_FakeClient` above is
    static/stateless and can't exercise a bug that only shows up ACROSS a
    call sequence -- exactly the class of bug the regression test below
    targets (a later add_ticket_tag call silently erasing an earlier one)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self._labels: list[str] = []

    async def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        self.calls.append((method, path, payload))
        if "labels" in path:
            if method == "GET":
                return {"payload": list(self._labels)}
            if method == "POST" and payload is not None:
                self._labels = [str(v) for v in payload.get("labels", [])]
        return {}


@pytest.mark.asyncio
async def test_classification_label_survives_a_later_unrelated_tag() -> None:
    """Regression: this is the EXACT finalize() sequence that broke --
    set_ticket_classification writes a division_<slug> label, then a later,
    unrelated add_ticket_tag call (e.g. record_csat_on_ticket's csat_<n>
    tag) must not erase it."""
    fake = _StatefulLabelsClient()
    adapter = ChatwootAdapter(Settings(chatwoot_account_id=1, chatwoot_inbox_id=7))
    adapter._request = fake._request  # type: ignore[method-assign]
    await adapter.set_ticket_classification("55", division="Sales")
    await adapter.add_ticket_tag("55", "csat_4")
    assert set(fake._labels) == {"division_sales", "csat_4"}


class _SlowStatefulClient:
    """Chatwoot with a REAL read-modify-write window.

    The response is snapshotted at request time and only handed back after
    yielding to the event loop -- which is what an in-flight HTTP GET
    actually is. Snapshotting after the yield instead would make every read
    see the latest state and silently serialise the writers, which is how a
    concurrency test ends up vacuous.
    """

    def __init__(self) -> None:
        self.attrs: dict[str, Any] = {}
        self.labels: list[str] = []

    async def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        if method == "GET" and path.endswith("/labels"):
            snapshot = list(self.labels)
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            return {"payload": snapshot}
        if method == "POST" and path.endswith("/labels") and payload is not None:
            self.labels = [str(v) for v in payload.get("labels", [])]
            return {}
        if method == "GET" and "/conversations/" in path:
            snapshot_attrs = dict(self.attrs)
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            return {"custom_attributes": snapshot_attrs}
        if method == "POST" and path.endswith("/custom_attributes") and payload is not None:
            self.attrs = dict(payload.get("custom_attributes") or {})
            return {}
        return {}


@pytest.mark.asyncio
async def test_concurrent_custom_attribute_writers_do_not_lose_a_key() -> None:
    """Whole-branch review fix (Important 8): merge-safe is not enough --
    two concurrent GET->union->POST cycles on the SAME conversation both
    read the old state and the loser's key vanishes. finalize()'s
    external_id/case_category write and the recording-status callback's
    recording_url write overlap at hangup, so this is a real interleaving,
    not a theoretical one. A per-ticket lock inside the adapter serialises
    them."""
    fake = _SlowStatefulClient()
    adapter = ChatwootAdapter(Settings(chatwoot_account_id=1, chatwoot_inbox_id=7))
    adapter._request = fake._request  # type: ignore[method-assign]
    await asyncio.gather(
        adapter._merge_custom_attributes("55", {"external_id": "phone-CA1"}),
        adapter._merge_custom_attributes("55", {"recording_url": "https://rec"}),
        adapter._merge_custom_attributes("55", {"case_category": "Aftersales"}),
    )
    assert fake.attrs == {
        "external_id": "phone-CA1",
        "recording_url": "https://rec",
        "case_category": "Aftersales",
    }


@pytest.mark.asyncio
async def test_concurrent_tag_writers_do_not_lose_a_label() -> None:
    """Same race on the labels endpoint: finalize()'s `division_<slug>` and
    the dial-status callback's `unanswered_handoff` are concurrent."""
    fake = _SlowStatefulClient()
    adapter = ChatwootAdapter(Settings(chatwoot_account_id=1, chatwoot_inbox_id=7))
    adapter._request = fake._request  # type: ignore[method-assign]
    await asyncio.gather(
        adapter.add_ticket_tag("55", "division_sales"),
        adapter.add_ticket_tag("55", "unanswered_handoff"),
        adapter.add_ticket_tag("55", "csat_4"),
    )
    assert set(fake.labels) == {"division_sales", "unanswered_handoff", "csat_4"}


@pytest.mark.asyncio
async def test_ticket_lock_map_is_bounded_and_never_evicts_a_held_lock() -> None:
    adapter = ChatwootAdapter(Settings(chatwoot_account_id=1, chatwoot_inbox_id=7))
    held = adapter._ticket_lock("HELD")
    await held.acquire()
    try:
        for i in range(chatwoot_module._TICKET_LOCK_CAP + 50):
            adapter._ticket_lock(f"T-{i}")
        assert len(adapter._ticket_locks) <= chatwoot_module._TICKET_LOCK_CAP + 2
        assert adapter._ticket_locks.get("HELD") is held
    finally:
        held.release()


@pytest.mark.asyncio
async def test_add_ticket_tag_read_failure_is_quiet_when_chatwoot_is_disabled() -> None:
    """Whole-branch review minor: `_merge_custom_attributes` already made
    this distinction; `add_ticket_tag` did not, so a deliberately
    Chatwoot-less tenant got a standing ERROR on every tag write."""
    adapter = ChatwootAdapter(
        Settings(chatwoot_account_id=1, chatwoot_inbox_id=7, chatwoot_enabled=False)
    )
    fake = _FakeClient({("GET", "/labels"): None})
    adapter._request = fake._request  # type: ignore[method-assign]
    with capture_logs() as captured:
        await adapter.add_ticket_tag("55", "vip")
    events = [e["event"] for e in captured]
    assert "chatwoot_add_ticket_tag_read_failed" not in events
    assert "chatwoot_add_ticket_tag_skipped_disabled" in events


@pytest.mark.asyncio
async def test_find_conversation_ticket_does_not_grow_the_session_cache() -> None:
    """Whole-branch review minor: every phone call has a unique
    `phone-<CallSid>` session id, so caching here added one permanent entry
    per call to a map nothing evicts -- and the id cached could be a
    RESOLVED conversation, which `_find_or_create_conversation`'s
    active-only reuse rule (sharing the same map) must not adopt."""
    fake = _FakeClient(
        {
            ("GET", "/contacts/search"): {"payload": [{"id": 3, "identifier": "phone-CA1"}]},
            ("GET", "/conversations"): {
                "payload": [{"id": 91, "source_id": "phone-CA1", "status": "resolved"}]
            },
        }
    )
    adapter = _adapter(fake)
    assert await adapter.find_conversation_ticket("phone-CA1") == "91"
    assert adapter._conv_by_session == {}


@pytest.mark.asyncio
async def test_rotate_conversation_ticket_busts_cache_and_recreates() -> None:
    fake = _FakeClient({("POST", "/conversations"): {"id": 77}})
    adapter = _adapter(fake)
    adapter._conv_by_session["web-1"] = "10"  # stale cached conversation
    tid = await adapter.rotate_conversation_ticket(
        session_id="web-1", subject="s", customer_name=None, customer_phone=None
    )
    assert tid == "77"
    assert adapter._conv_by_session["web-1"] == "77"
    assert any(p.endswith("/conversations") for _, p, _ in fake.calls)


@pytest.mark.asyncio
async def test_get_latest_public_comment_empty_returns_blank_tuple() -> None:
    fake = _FakeClient({("GET", "/messages"): {"payload": []}})
    result = await _adapter(fake).get_latest_public_comment("55")
    assert result == ("", None, None)


@pytest.mark.asyncio
async def test_get_latest_public_comment_returns_latest_incoming() -> None:
    fake = _FakeClient(
        {
            ("GET", "/messages"): {
                "payload": [
                    {"message_type": 1, "content": "agent reply", "sender": {"name": "A"}},
                    {
                        "message_type": 0,
                        "content": "customer question",
                        "sender": {"name": "Cust", "email": "c@example.com"},
                    },
                ]
            }
        }
    )
    content, name, email = await _adapter(fake).get_latest_public_comment("55")
    assert content == "customer question"
    assert name == "Cust"
    assert email == "c@example.com"


@pytest.mark.asyncio
async def test_post_public_reply_resolves_when_solved() -> None:
    fake = _FakeClient({})
    await _adapter(fake).post_public_reply("55", "resolved reply", status="solved")
    paths = [p for _, p, _ in fake.calls]
    assert any("/messages" in p for p in paths)  # public reply sent
    assert any("toggle_status" in p for p in paths)  # conversation resolved


@pytest.mark.asyncio
async def test_set_ticket_external_id_posts_custom_attribute() -> None:
    fake = _FakeClient({})
    await _adapter(fake).set_ticket_external_id("55", "whatsapp-+60123")
    method, path, payload = fake.calls[-1]
    assert method == "POST" and "custom_attributes" in path
    assert payload == {"custom_attributes": {"external_id": "whatsapp-+60123"}}


@pytest.mark.asyncio
async def test_set_ticket_external_id_merges_rather_than_clobbers() -> None:
    """The Critical fix: Chatwoot's custom-attributes endpoint REPLACES the
    whole object. Writing external_id alone, without reading what's already
    there first, would silently erase case_category/recording_sid/anything
    else already on the conversation."""
    fake = _FakeClient(
        {("GET", "/conversations/55"): {"custom_attributes": {"case_category": "Sales"}}}
    )
    await _adapter(fake).set_ticket_external_id("55", "whatsapp-+60123")
    method, path, payload = fake.calls[-1]
    assert method == "POST" and path.endswith("/custom_attributes")
    assert payload == {
        "custom_attributes": {"case_category": "Sales", "external_id": "whatsapp-+60123"}
    }


@pytest.mark.asyncio
async def test_custom_attributes_write_skipped_entirely_when_read_fails() -> None:
    """If the GET fails we cannot know what's already there -- posting a
    set we can't prove is complete would silently wipe everything else.
    Skip the write entirely, same posture as add_ticket_tag's read-failure
    guard."""
    fake = _FakeClient({("GET", "/conversations/55"): None})
    await _adapter(fake).set_ticket_external_id("55", "whatsapp-+60123")
    assert len(fake.calls) == 1  # the GET only -- no POST followed it
    assert fake.calls[0][0] == "GET"


@pytest.mark.asyncio
async def test_set_ticket_classification_all_none_is_a_no_op() -> None:
    fake = _FakeClient({})
    await _adapter(fake).set_ticket_classification("55")
    assert fake.calls == []


@pytest.mark.asyncio
async def test_set_ticket_classification_posts_custom_attributes_and_division_label() -> None:
    """The load-bearing detail: mapping.py's Package E reporting reads
    `case_category` (canonical spelling) or a `division_<slug>` label --
    NEVER the `division` custom attribute, which is the Cases List UI's own
    field and wants the deck's DISPLAY spelling instead."""
    fake = _FakeClient({})
    await _adapter(fake).set_ticket_classification(
        "55", case_type="Complaint", division="Aftersales", concern="Service Operation"
    )
    custom_attr_calls = [c for c in fake.calls if "custom_attributes" in c[1]]
    assert len(custom_attr_calls) == 1
    method, _path, payload = custom_attr_calls[0]
    assert method == "POST" and payload == {
        "custom_attributes": {
            "case_type": "Complaint",
            "division": "After Sales",
            "case_category": "Aftersales",
            "concern": "Service Operation",
        }
    }
    label_calls = [c for c in fake.calls if "labels" in c[1]]
    # add_ticket_tag is GET-then-POST now (it must not clobber other labels
    # on the conversation), so there are two label calls, not one.
    assert [c[0] for c in label_calls] == ["GET", "POST"]
    method, _path, payload = label_calls[-1]
    assert method == "POST" and payload == {"labels": ["division_aftersales"]}


@pytest.mark.asyncio
async def test_set_call_recording_posts_custom_attributes_only() -> None:
    """The load-bearing detail (Package C Task 5 compliance): a recording's
    sid/duration/url must land ONLY as custom attributes, never as a
    comment/note -- a private note is still agent-visible without the
    call_recording.listen permission, which would defeat the whole point of
    gating retrieval."""
    fake = _FakeClient({})
    await _adapter(fake).set_call_recording(
        "55",
        recording_sid="RE123",
        recording_duration="42",
        recording_url="https://api.twilio.com/2010-04-01/Accounts/AC1/Recordings/RE123",
    )
    # A merge-safe write reads the conversation FIRST (see
    # test_set_call_recording_merges_rather_than_clobbers for the union
    # itself); what this test pins is that recording data ONLY ever reaches
    # custom_attributes, never /messages (a comment/note).
    assert not any(p.endswith("/messages") for _m, p, _pl in fake.calls)
    method, path, payload = fake.calls[-1]
    assert method == "POST" and path.endswith("/custom_attributes")
    assert payload == {
        "custom_attributes": {
            "recording_sid": "RE123",
            "recording_duration": "42",
            "recording_url": ("https://api.twilio.com/2010-04-01/Accounts/AC1/Recordings/RE123"),
        }
    }


@pytest.mark.asyncio
async def test_set_call_recording_merges_rather_than_clobbers() -> None:
    """Critical fix (review round 1): the recording-status callback fires at
    or after call end -- i.e. AFTER finalize() has already written
    case_type/case_category/external_id on the same conversation. Blindly
    assigning custom_attributes here would blank every one of them (and
    everything Package E's reporting reads) on EVERY recorded call."""
    fake = _FakeClient(
        {
            ("GET", "/conversations/55"): {
                "custom_attributes": {
                    "case_type": "Complaint",
                    "case_category": "Aftersales",
                    "external_id": "phone-CA1",
                }
            }
        }
    )
    await _adapter(fake).set_call_recording(
        "55", recording_sid="RE123", recording_duration="42", recording_url="https://x/RE123"
    )
    method, path, payload = fake.calls[-1]
    assert method == "POST" and path.endswith("/custom_attributes")
    assert payload == {
        "custom_attributes": {
            "case_type": "Complaint",
            "case_category": "Aftersales",
            "external_id": "phone-CA1",
            "recording_sid": "RE123",
            "recording_duration": "42",
            "recording_url": "https://x/RE123",
        }
    }


@pytest.mark.asyncio
async def test_set_ticket_classification_division_without_display_override_passes_through() -> None:
    """Every division except Aftersales/"After Sales" has an identical
    display and canonical spelling -- pin that the untranslated path really
    is the identity, not a hardcoded special case that happens to work once."""
    fake = _FakeClient({})
    await _adapter(fake).set_ticket_classification("55", division="Sales")
    custom_attr_calls = [c for c in fake.calls if "custom_attributes" in c[1]]
    _, _, payload = custom_attr_calls[0]
    assert payload == {"custom_attributes": {"division": "Sales", "case_category": "Sales"}}
    label_calls = [c for c in fake.calls if "labels" in c[1]]
    _, _, payload = label_calls[-1]
    assert payload == {"labels": ["division_sales"]}


@pytest.mark.asyncio
async def test_set_ticket_classification_concern_only_writes_no_division_label() -> None:
    fake = _FakeClient({})
    await _adapter(fake).set_ticket_classification("55", concern="Booking")
    assert not any("labels" in p for _m, p, _pl in fake.calls)
    method, path, payload = fake.calls[-1]
    assert method == "POST" and path.endswith("/custom_attributes")
    assert payload == {"custom_attributes": {"concern": "Booking"}}


# --- find_conversation_ticket (Package C Task 5 review fix, Critical 2) ----
#
# The recording-status callback can fire well after the call (and often
# after finalize() has already RESOLVED the conversation) on a process that
# never handled the original WebSocket -- so the in-process
# `_conv_by_session` cache cannot be relied on. These pin the real
# ChatwootAdapter's behaviour directly (not a test-only fake sentinel, per
# the review's explicit "that is the Task 3 trap again" note): it must
# find a conversation in ANY status, and it must NEVER create one.


@pytest.mark.asyncio
async def test_find_conversation_ticket_returns_cached_without_a_request() -> None:
    fake = _FakeClient({})
    adapter = _adapter(fake)
    adapter._conv_by_session["phone-CA1"] = "T-1"
    tid = await adapter.find_conversation_ticket("phone-CA1")
    assert tid == "T-1"
    assert fake.calls == []  # cache hit -- no network calls at all


@pytest.mark.asyncio
async def test_find_conversation_ticket_returns_none_when_no_contact_matches() -> None:
    """Never fabricates an id: no Chatwoot contact for this session at all."""
    fake = _FakeClient({("GET", "/contacts/search"): {"payload": []}})
    tid = await _adapter(fake).find_conversation_ticket("phone-CA404")
    assert tid is None


@pytest.mark.asyncio
async def test_find_conversation_ticket_returns_none_when_contact_has_no_matching_conversation() -> (
    None
):
    fake = _FakeClient(
        {
            ("GET", "/contacts/search"): {"payload": [{"id": 9, "identifier": "phone-CA1"}]},
            ("GET", "/contacts/9/conversations"): {"payload": []},
        }
    )
    tid = await _adapter(fake).find_conversation_ticket("phone-CA1")
    assert tid is None


@pytest.mark.asyncio
async def test_find_conversation_ticket_finds_a_resolved_conversation() -> None:
    """The Critical fix itself: unlike ensure_conversation_ticket's reuse
    check (deliberately ACTIVE-only -- a resolved conversation is a closed
    ticket a NEW customer message should not land back in),
    find_conversation_ticket must find the SAME conversation this call
    already produced even after finalize() has resolved it -- otherwise the
    recording-status callback would create a fresh, empty conversation
    instead of attaching to the real one."""
    fake = _FakeClient(
        {
            ("GET", "/contacts/search"): {"payload": [{"id": 9, "identifier": "phone-CA1"}]},
            ("GET", "/contacts/9/conversations"): {
                "payload": [
                    {"id": 55, "source_id": "phone-CA1", "status": "resolved", "inbox_id": 7}
                ]
            },
        }
    )
    tid = await _adapter(fake).find_conversation_ticket("phone-CA1")
    assert tid == "55"


@pytest.mark.asyncio
async def test_find_conversation_ticket_matches_on_source_id_not_any_conversation() -> None:
    """A contact can have conversations from other channels/sessions --
    only the exact source_id match is this session's own conversation."""
    fake = _FakeClient(
        {
            ("GET", "/contacts/search"): {"payload": [{"id": 9, "identifier": "phone-CA1"}]},
            ("GET", "/contacts/9/conversations"): {
                "payload": [
                    {"id": 10, "source_id": "web-other-session", "status": "open"},
                    {"id": 55, "source_id": "phone-CA1", "status": "open"},
                ]
            },
        }
    )
    tid = await _adapter(fake).find_conversation_ticket("phone-CA1")
    assert tid == "55"


@pytest.mark.asyncio
async def test_find_conversation_ticket_never_issues_a_create_call() -> None:
    fake = _FakeClient({("GET", "/contacts/search"): {"payload": []}})
    await _adapter(fake).find_conversation_ticket("phone-CA1")
    assert not any(m == "POST" for m, _p, _pl in fake.calls)
