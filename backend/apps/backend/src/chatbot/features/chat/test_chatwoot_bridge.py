from __future__ import annotations

from typing import Any

import pytest

from chatbot.features.chat.adapters.chatwoot import ChatwootAdapter
from chatbot.features.chat.models import HandoffOpenPayload, Message
from chatbot.platform.config import Settings


class _FakeClient:
    def __init__(self, responses: dict[tuple[str, str], dict[str, Any]]) -> None:
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


def _adapter(secret: str = "s3cret") -> ChatwootAdapter:  # noqa: S107
    return ChatwootAdapter(
        Settings(chatwoot_account_id=1, chatwoot_inbox_id=7, chatwoot_webhook_secret=secret)
    )


def test_verify_webhook_signature() -> None:
    a = _adapter()
    assert a.verify_webhook_signature(b"body", "s3cret") is True
    assert a.verify_webhook_signature(b"body", "wrong") is False


def test_verify_open_when_no_secret() -> None:
    assert _adapter(secret="").verify_webhook_signature(b"body", None) is True


def test_parse_webhook_events_agent_only() -> None:
    a = _adapter()
    payload = {
        "event": "message_created",
        "message_type": "outgoing",
        "private": False,
        "content": "Agent here",
        "conversation": {"id": 42},
        "sender": {"name": "Aina"},
    }
    events = a.parse_webhook_events(payload)
    assert len(events) == 1
    assert events[0].conversation_id == "42"
    assert events[0].text == "Agent here"
    assert events[0].author_name == "Aina"


def test_parse_ignores_incoming_and_private() -> None:
    a = _adapter()
    for mt, private in [("incoming", False), ("outgoing", True)]:
        payload = {
            "event": "message_created",
            "message_type": mt,
            "private": private,
            "content": "x",
            "conversation": {"id": 42},
            "sender": {"name": "n"},
        }
        assert a.parse_webhook_events(payload) == []


@pytest.mark.asyncio
async def test_forward_customer_message_posts_incoming() -> None:
    a = _adapter()
    fake = _FakeClient({})
    a._request = fake._request  # type: ignore[method-assign]
    await a.forward_customer_message("42", "user-1", "hello agent")
    method, path, payload = fake.calls[-1]
    assert method == "POST" and "/conversations/42/messages" in path
    assert payload == {"content": "hello agent", "message_type": "incoming"}


@pytest.mark.asyncio
async def test_open_handoff_posts_incoming_customer_message_before_labels() -> None:
    adapter = ChatwootAdapter(
        Settings(chatwoot_account_id=1, chatwoot_inbox_id=7, chatwoot_agent_team_id=3)
    )
    fake = _FakeClient({("POST", "/conversations"): {"id": 99}})
    adapter._request = fake._request  # type: ignore[method-assign]
    payload = HandoffOpenPayload(
        session_id="sim-1",
        customer_name="Aina",
        customer_email="",
        ai_summary="Customer wants a human.",
        transcript=(
            Message(role="user", text="my ring broke"),
            Message(role="assistant", text="I'm sorry to hear that"),
        ),
    )
    await adapter.open_handoff(payload)
    calls = fake.calls
    inc = next(
        i
        for i, (_m, p, pl) in enumerate(calls)
        if p.endswith("/messages") and (pl or {}).get("message_type") == "incoming"
    )
    lbl = next(i for i, (m, p, _pl) in enumerate(calls) if p.endswith("/labels") and m == "POST")
    assert inc < lbl, "incoming message must precede the label (webhook trigger)"
    inc_payload = calls[inc][2]
    assert inc_payload is not None and inc_payload["content"] == "my ring broke"


@pytest.mark.asyncio
async def test_open_handoff_marks_conversation_as_ai_handoff() -> None:
    # The handoff-created conversation must carry the ``ai_handoff`` marker in its
    # additional_attributes so the agent sync service (which sees the
    # conversation_created webhook) skips its AI-disclaimer greeting — otherwise
    # that greeting surfaces as the human agent's opening message to the customer.
    adapter = ChatwootAdapter(
        Settings(chatwoot_account_id=1, chatwoot_inbox_id=7, chatwoot_agent_team_id=3)
    )
    fake = _FakeClient({("POST", "/conversations"): {"id": 99}})
    adapter._request = fake._request  # type: ignore[method-assign]
    payload = HandoffOpenPayload(
        session_id="sim-1",
        customer_name="Aina",
        customer_email="",
        ai_summary="Customer wants a human.",
        transcript=(Message(role="user", text="connect me to a human"),),
    )
    await adapter.open_handoff(payload)
    create = next(pl for m, p, pl in fake.calls if m == "POST" and p.endswith("/conversations"))
    assert create is not None
    assert create.get("additional_attributes") == {"ai_handoff": True}


@pytest.mark.asyncio
async def test_open_handoff_writes_dimension_labels_in_single_final_call() -> None:
    # The bridge path (Chatwoot escalation) must persist division/department/sla
    # as labels the metrics sync can read back, in the ONE final labels call — a
    # second labels POST would needlessly re-fire the webhook. category/
    # subcategory have moved to custom attributes (see the test below).
    adapter = ChatwootAdapter(
        Settings(
            chatwoot_account_id=1,
            chatwoot_inbox_id=7,
            chatwoot_escalation_label="ai-escalation",
            chatwoot_complaint_label="escalate",
        )
    )
    fake = _FakeClient({("POST", "/conversations"): {"id": 99}})
    adapter._request = fake._request  # type: ignore[method-assign]
    payload = HandoffOpenPayload(
        session_id="sim-1",
        customer_name="Aina",
        customer_email="",
        ai_summary="Customer is unhappy.",
        transcript=(Message(role="user", text="my battery is dead"),),
        category="Aftersales",
        subcategory="Battery",
        division="Aftersales",
        department="Service Center",
        sla_minutes=480,
        reason="negative_sentiment",  # complaint -> complaint label applied
    )
    await adapter.open_handoff(payload)
    # Whole-branch review fix (Important 9): the single labels write is now
    # preceded by a GET of the current set (merge-safe union). Filter on the
    # POST -- the "exactly one labels POST" batching invariant is unchanged.
    labels_calls = [pl for m, p, pl in fake.calls if p.endswith("/labels") and m == "POST"]
    assert len(labels_calls) == 1
    labels = labels_calls[0]["labels"]  # type: ignore[index]
    assert labels == [
        "division_aftersales",
        "dept_service_center",
        "sla_480",
        "ai-escalation",
        "escalate",
    ]
    assert not any(lbl.startswith("category_") for lbl in labels)
    assert not any(lbl.startswith("subcat_") for lbl in labels)
    custom_attrs_calls = [pl for _m, p, pl in fake.calls if p.endswith("/custom_attributes")]
    assert len(custom_attrs_calls) == 1, "ONE custom_attributes call, not two"
    ca = custom_attrs_calls[0]
    assert ca == {
        "custom_attributes": {
            "sla_minutes": 480,
            "case_category": "Aftersales",
            "case_subcategory": "Battery",
        }
    }


@pytest.mark.asyncio
async def test_open_handoff_writes_case_type_and_vehicle_model_as_custom_attributes() -> None:
    # case_type/vehicle_model must ride in the SAME custom_attributes call as
    # case_category/case_subcategory/sla_minutes (see the dimension-labels test).
    adapter = ChatwootAdapter(
        Settings(chatwoot_account_id=1, chatwoot_inbox_id=7, chatwoot_agent_team_id=3)
    )
    fake = _FakeClient({("POST", "/conversations"): {"id": 99}})
    adapter._request = fake._request  # type: ignore[method-assign]
    payload = HandoffOpenPayload(
        session_id="sim-1",
        customer_name="Aina",
        customer_email="",
        ai_summary="Customer is unhappy.",
        transcript=(Message(role="user", text="my battery is dead"),),
        category="Aftersales",
        subcategory="Battery",
        case_type="Complaint",
        vehicle_model="e.MAS 7",
        division="Aftersales",
        department="Service Center",
        sla_minutes=480,
    )
    await adapter.open_handoff(payload)
    custom_attrs_calls = [pl for _m, p, pl in fake.calls if p.endswith("/custom_attributes")]
    assert len(custom_attrs_calls) == 1, "ONE custom_attributes call, not two"
    ca = custom_attrs_calls[0]
    assert ca == {
        "custom_attributes": {
            "sla_minutes": 480,
            "case_category": "Aftersales",
            "case_subcategory": "Battery",
            "case_type": "Complaint",
            "vehicle_model": "e.MAS 7",
        }
    }


@pytest.mark.asyncio
async def test_open_handoff_plain_human_request_stays_chatwoot_only() -> None:
    # A "talk to a human" handoff (help_request, non-urgent) must NOT get the
    # complaint label — it stays a live Chatwoot conversation only.
    adapter = ChatwootAdapter(
        Settings(
            chatwoot_account_id=1,
            chatwoot_inbox_id=7,
            chatwoot_escalation_label="ai-escalation",
            chatwoot_complaint_label="escalate",
        )
    )
    fake = _FakeClient({("POST", "/conversations"): {"id": 99}})
    adapter._request = fake._request  # type: ignore[method-assign]
    payload = HandoffOpenPayload(
        session_id="sim-2",
        customer_name="Aina",
        customer_email="",
        ai_summary="Customer wants a human.",
        transcript=(Message(role="user", text="connect me with a human agent"),),
        urgency="medium",
        reason="help_request",
    )
    await adapter.open_handoff(payload)
    # Whole-branch review fix (Important 9): the single labels write is now
    # preceded by a GET of the current set (merge-safe union). Filter on the
    # POST -- the "exactly one labels POST" batching invariant is unchanged.
    labels_calls = [pl for m, p, pl in fake.calls if p.endswith("/labels") and m == "POST"]
    assert len(labels_calls) == 1
    assert labels_calls[0]["labels"] == ["ai-escalation"]  # type: ignore[index]
