from __future__ import annotations

from typing import Any

import pytest

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
    assert fake.calls == [
        (
            "POST",
            "/conversations/55/custom_attributes",
            {
                "custom_attributes": {
                    "recording_sid": "RE123",
                    "recording_duration": "42",
                    "recording_url": (
                        "https://api.twilio.com/2010-04-01/Accounts/AC1/Recordings/RE123"
                    ),
                }
            },
        )
    ]


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
    assert fake.calls == [
        (
            "POST",
            "/conversations/55/custom_attributes",
            {"custom_attributes": {"concern": "Booking"}},
        )
    ]
