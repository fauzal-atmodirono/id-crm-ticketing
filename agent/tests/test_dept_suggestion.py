"""Tests for `dept_suggestion.maybe_suggest_department` (AI-suggested
escalation department, suggest-only). See the module docstring for the
design: the label is never applied automatically, candidates always come
from the PIC store (never a static list), the suggestion is idempotent per
conversation, and every path is fail-open + flag-gated.
"""

import json
from unittest.mock import AsyncMock

import httpx
import respx

from app.clients.deps import get_proton_config_client
from app.config import get_settings
from app.services import dept_suggestion

CHATWOOT = "http://chatwoot-rails:3000"
PROTON = "http://proton-backend:8080"

MESSAGES_RESPONSE = {
    "payload": [
        {
            "id": 1,
            "content": "My car won't start, engine light is on",
            "message_type": 0,
            "private": False,
            "created_at": 1_700_000_000,
            "sender": {"id": 55, "name": "Jane Doe", "email": "jane@example.com"},
        },
    ]
}


def _payload(conversation_id=20, inbox_id=7, message_type="incoming", content=None):
    return {
        "event": "message_created",
        "id": 501,
        "content": content or "My car won't start, engine light is on",
        "message_type": message_type,
        "private": False,
        "conversation": {"id": conversation_id},
        "inbox": {"id": inbox_id},
        "sender": {"id": 55, "name": "Jane Doe", "type": "contact"},
    }


def _enable(monkeypatch):
    monkeypatch.setattr(get_settings(), "dept_suggestion_enabled", True)
    monkeypatch.setattr(get_settings(), "proton_backend_url", PROTON)
    monkeypatch.setattr(get_settings(), "proton_backend_key", "k")
    get_proton_config_client.cache_clear()


def _mock_email_conversation(conversation_id=20, inbox_id=7, labels=None, custom_attributes=None):
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/inboxes/{inbox_id}").mock(
        return_value=httpx.Response(200, json={"id": inbox_id, "channel_type": "Channel::Email"})
    )
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/{conversation_id}/labels").mock(
        return_value=httpx.Response(200, json={"payload": labels or []})
    )
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/{conversation_id}").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": conversation_id,
                "inbox_id": inbox_id,
                "custom_attributes": custom_attributes or {},
            },
        )
    )
    respx.get(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/{conversation_id}/messages"
    ).mock(return_value=httpx.Response(200, json=MESSAGES_RESPONSE))


@respx.mock
async def test_clean_suggestion_posts_exactly_one_note_naming_a_candidate(monkeypatch):
    _enable(monkeypatch)
    _mock_email_conversation()
    respx.get(f"{PROTON}/escalation/departments").mock(
        return_value=httpx.Response(
            200, json={"departments": ["engineer", "pre_sales", "sales"]}
        )
    )
    monkeypatch.setattr(dept_suggestion.gemini, "generate", AsyncMock(return_value="engineer"))
    note_route = respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/20/messages"
    ).mock(return_value=httpx.Response(200, json={"id": 99}))
    attrs_route = respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/20/custom_attributes"
    ).mock(return_value=httpx.Response(200, json={}))

    await dept_suggestion.maybe_suggest_department(_payload())

    assert note_route.call_count == 1
    body = json.loads(note_route.calls.last.request.content)
    assert body["private"] is True
    assert "engineer" in body["content"]
    assert "dept_engineer" in body["content"]
    assert "escalate" in body["content"]
    assert attrs_route.call_count == 1
    stamped = json.loads(attrs_route.calls.last.request.content)["custom_attributes"]
    assert "dept_suggested_at" in stamped
    get_proton_config_client.cache_clear()


@respx.mock
async def test_existing_dept_label_suppresses_suggestion(monkeypatch):
    _enable(monkeypatch)
    _mock_email_conversation(labels=["dept_sales", "escalate"])
    departments_route = respx.get(f"{PROTON}/escalation/departments")
    note_route = respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/20/messages")

    await dept_suggestion.maybe_suggest_department(_payload())

    assert not note_route.called
    assert not departments_route.called
    get_proton_config_client.cache_clear()


@respx.mock
async def test_stamp_suppresses_a_second_suggestion(monkeypatch):
    _enable(monkeypatch)
    _mock_email_conversation(custom_attributes={"dept_suggested_at": "2026-08-01T00:00:00+00:00"})
    departments_route = respx.get(f"{PROTON}/escalation/departments")
    note_route = respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/20/messages")

    await dept_suggestion.maybe_suggest_department(_payload())

    assert not note_route.called
    assert not departments_route.called
    get_proton_config_client.cache_clear()


@respx.mock
async def test_empty_candidate_list_posts_nothing(monkeypatch):
    _enable(monkeypatch)
    _mock_email_conversation()
    respx.get(f"{PROTON}/escalation/departments").mock(
        return_value=httpx.Response(200, json={"departments": []})
    )
    gen = AsyncMock(return_value="engineer")
    monkeypatch.setattr(dept_suggestion.gemini, "generate", gen)
    note_route = respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/20/messages")

    await dept_suggestion.maybe_suggest_department(_payload())

    assert not note_route.called
    assert not gen.called
    get_proton_config_client.cache_clear()


@respx.mock
async def test_gemini_answer_outside_candidates_posts_nothing(monkeypatch):
    _enable(monkeypatch)
    _mock_email_conversation()
    respx.get(f"{PROTON}/escalation/departments").mock(
        return_value=httpx.Response(200, json={"departments": ["engineer", "sales"]})
    )
    monkeypatch.setattr(
        dept_suggestion.gemini, "generate", AsyncMock(return_value="aftersales")
    )
    note_route = respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/20/messages")

    await dept_suggestion.maybe_suggest_department(_payload())

    assert not note_route.called
    get_proton_config_client.cache_clear()


@respx.mock
async def test_unreachable_backend_posts_nothing(monkeypatch):
    _enable(monkeypatch)
    _mock_email_conversation()
    respx.get(f"{PROTON}/escalation/departments").mock(return_value=httpx.Response(503))
    note_route = respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/20/messages")

    await dept_suggestion.maybe_suggest_department(_payload())

    assert not note_route.called
    get_proton_config_client.cache_clear()


@respx.mock
async def test_flag_off_does_nothing(monkeypatch):
    monkeypatch.setattr(get_settings(), "dept_suggestion_enabled", False)
    inbox_route = respx.get(f"{CHATWOOT}/api/v1/accounts/1/inboxes/7")

    await dept_suggestion.maybe_suggest_department(_payload())

    assert not inbox_route.called


@respx.mock
async def test_non_email_inbox_does_nothing(monkeypatch):
    _enable(monkeypatch)
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/inboxes/7").mock(
        return_value=httpx.Response(200, json={"id": 7, "channel_type": "Channel::TwilioSms"})
    )
    departments_route = respx.get(f"{PROTON}/escalation/departments")
    note_route = respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/20/messages")

    await dept_suggestion.maybe_suggest_department(_payload())

    assert not note_route.called
    assert not departments_route.called
    get_proton_config_client.cache_clear()


@respx.mock
async def test_non_incoming_message_does_nothing(monkeypatch):
    _enable(monkeypatch)
    inbox_route = respx.get(f"{CHATWOOT}/api/v1/accounts/1/inboxes/7")

    await dept_suggestion.maybe_suggest_department(_payload(message_type="outgoing"))

    assert not inbox_route.called
    get_proton_config_client.cache_clear()


@respx.mock
async def test_missing_conversation_or_inbox_id_does_nothing(monkeypatch):
    _enable(monkeypatch)
    inbox_route = respx.get(f"{CHATWOOT}/api/v1/accounts/1/inboxes/7")

    payload = _payload()
    payload["conversation"] = {}
    await dept_suggestion.maybe_suggest_department(payload)

    assert not inbox_route.called
    get_proton_config_client.cache_clear()


@respx.mock
async def test_classify_department_returns_none_for_empty_candidates():
    assert await dept_suggestion.classify_department("hello", []) is None


@respx.mock
async def test_classify_department_returns_none_on_gemini_error(monkeypatch):
    monkeypatch.setattr(
        dept_suggestion.gemini, "generate", AsyncMock(side_effect=RuntimeError("boom"))
    )
    assert await dept_suggestion.classify_department("hello", ["engineer"]) is None
