from unittest.mock import AsyncMock

import pytest

from app.services import lifecycle, lifecycle_store


@pytest.fixture
def chatwoot(monkeypatch):
    client = AsyncMock()
    monkeypatch.setattr(lifecycle, "get_chatwoot_client", lambda: client)
    monkeypatch.setattr(lifecycle, "get_proton_config_client", lambda: None)
    return client


@pytest.fixture
def email_on(monkeypatch):
    s = lifecycle.get_settings()
    monkeypatch.setattr(s, "email_autoack_enabled", True, raising=False)
    monkeypatch.setattr(s, "lifecycle_disclaimer_enabled", True, raising=False)


async def test_email_inbox_posts_autoack(chatwoot, email_on):
    chatwoot.get_inbox.return_value = {"channel_type": "Channel::Email"}
    await lifecycle.on_conversation_created({"id": 40, "inbox_id": 2, "channel": "Channel::Email"})
    assert await lifecycle_store.get_state(40) == "active"
    # posted the email auto-ack (not the AI disclaimer)
    args, kwargs = chatwoot.create_message.await_args
    assert args[0] == 40
    assert "acknowledge receipt of your enquiry" in args[1]


async def test_chat_inbox_still_posts_disclaimer(chatwoot, email_on):
    chatwoot.get_inbox.return_value = {"channel_type": "Channel::Whatsapp"}
    await lifecycle.on_conversation_created({"id": 41, "inbox_id": 3, "channel": "Channel::Whatsapp"})
    args, kwargs = chatwoot.create_message.await_args
    assert "artificial intelligence" in args[1].lower()  # AI disclaimer


async def test_email_autoack_disabled_posts_nothing(chatwoot, monkeypatch):
    s = lifecycle.get_settings()
    monkeypatch.setattr(s, "email_autoack_enabled", False, raising=False)
    monkeypatch.setattr(s, "lifecycle_disclaimer_enabled", True, raising=False)
    chatwoot.get_inbox.return_value = {"channel_type": "Channel::Email"}
    await lifecycle.on_conversation_created({"id": 42, "inbox_id": 2, "channel": "Channel::Email"})
    # Email inbox with auto-ack disabled must post nothing — not even the AI
    # disclaimer (which is wrong for an email thread). lifecycle_disclaimer_enabled
    # is True here to prove the email branch returns before the disclaimer path.
    chatwoot.create_message.assert_not_awaited()
