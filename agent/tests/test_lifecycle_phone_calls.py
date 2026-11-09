"""Phone-call conversations get none of the chat lifecycle messages.

A call's conversation is created by the backend phone bridge on the API inbox
to hold the transcript. The caller is on a phone, not in a chat window: the
AI disclaimer, the idle warning, the "rate our agent" survey and every other
customer-facing lifecycle post would land in a conversation nobody reads on
the customer side (and confuses the agent reading the case).
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from app.services import lifecycle, lifecycle_scanner, lifecycle_store


@pytest.fixture
def chatwoot(monkeypatch):
    client = AsyncMock()
    client.get_inbox.return_value = {"channel_type": "Channel::Api", "working_hours_enabled": False}
    monkeypatch.setattr(lifecycle, "get_chatwoot_client", lambda: client)
    monkeypatch.setattr(lifecycle, "get_proton_config_client", lambda: None)
    return client


@pytest.fixture(autouse=True)
def _enable(monkeypatch):
    from app.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "lifecycle_disclaimer_enabled", True, raising=False)
    monkeypatch.setattr(s, "lifecycle_survey_enabled", True, raising=False)


@pytest.mark.parametrize(
    "conv",
    [
        {"additional_attributes": {"session_id": "phone-CA123"}},
        {"custom_attributes": {"external_id": "phone-CA123"}},
        {"custom_attributes": {"caller_phone_source": "caller_id"}},
    ],
)
def test_phone_call_markers(conv):
    assert lifecycle.is_phone_call_conversation(conv)


@pytest.mark.parametrize(
    "conv",
    [
        {},
        {"additional_attributes": {"session_id": "whatsapp-1"}},
        {"additional_attributes": None, "custom_attributes": None},
        {"custom_attributes": {"external_id": "12345"}},
        "not a dict",
    ],
)
def test_non_phone_conversations(conv):
    assert not lifecycle.is_phone_call_conversation(conv)


async def test_phone_call_created_posts_no_disclaimer_and_is_closed(chatwoot):
    payload = {
        "id": 91,
        "inbox_id": 1,
        "channel": "Channel::Api",
        "additional_attributes": {"session_id": "phone-CA9"},
    }
    await lifecycle.on_conversation_created(payload)
    chatwoot.create_message.assert_not_awaited()
    # CLOSED so neither the scanner nor the resolve hook acts on it later,
    # even when their payloads carry no marker.
    assert await lifecycle_store.get_state(91) == lifecycle.CLOSED


async def test_chat_conversation_still_gets_the_disclaimer(chatwoot):
    await lifecycle.on_conversation_created({"id": 92, "inbox_id": 1, "channel": "Channel::Api"})
    chatwoot.create_message.assert_awaited_once()


async def test_resolved_phone_call_gets_no_rating_survey(chatwoot):
    # A call conversation created before this marker existed has an ACTIVE row
    # (or none); the resolve payload's custom attributes still identify it.
    await lifecycle_store.seed_active(93, channel="Channel::Api")
    await lifecycle.on_human_resolved(
        {"id": 93, "status": "resolved", "custom_attributes": {"external_id": "phone-CA1"}}
    )
    chatwoot.create_message.assert_not_awaited()
    assert await lifecycle_store.get_state(93) == lifecycle.CLOSED


async def test_resolved_chat_conversation_still_gets_the_survey(chatwoot):
    await lifecycle_store.seed_active(94, channel="Channel::Api")
    await lifecycle.on_human_resolved({"id": 94, "status": "resolved"})
    chatwoot.create_message.assert_awaited_once()


async def test_scanner_skips_phone_call_conversations(monkeypatch):
    client = AsyncMock()
    now = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
    idle_since = int(now.timestamp()) - 60 * 60
    client.list_conversations.side_effect = lambda status=None, assignee_type=None: (
        {"data": {"payload": [
            {"id": 95, "inbox_id": 1, "last_activity_at": idle_since, "meta": {"assignee": None},
             "additional_attributes": {"session_id": "phone-CA5"}},
        ]}} if status == "open" else {"data": {"payload": []}}
    )
    client.get_inbox.return_value = {"channel_type": "Channel::Api", "working_hours_enabled": False}
    monkeypatch.setattr(lifecycle_scanner, "get_chatwoot_client", lambda: client)
    monkeypatch.setattr(lifecycle, "get_chatwoot_client", lambda: client)
    monkeypatch.setattr(lifecycle_scanner, "_now", lambda: now)
    await lifecycle_scanner.scan_once()
    client.create_message.assert_not_awaited()
    assert await lifecycle_store.get_state(95) in (None, lifecycle.CLOSED)
