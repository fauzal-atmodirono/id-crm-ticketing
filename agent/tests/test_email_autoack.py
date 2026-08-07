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
    chatwoot.create_message.assert_awaited_once()
    args, kwargs = chatwoot.create_message.await_args
    assert args[0] == 40
    assert "acknowledge receipt of your enquiry" in args[1]


async def test_chat_inbox_still_posts_disclaimer(chatwoot, email_on):
    chatwoot.get_inbox.return_value = {"channel_type": "Channel::Whatsapp"}
    await lifecycle.on_conversation_created({"id": 41, "inbox_id": 3, "channel": "Channel::Whatsapp"})
    chatwoot.create_message.assert_awaited_once()
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


async def test_email_autoack_uses_store_template_when_present(chatwoot, email_on, monkeypatch):
    """An operator-edited store template overrides the env default verbatim."""
    proton = AsyncMock()
    proton.get_email_autoack_template.return_value = "Operator-edited ack body"
    monkeypatch.setattr(lifecycle, "get_proton_config_client", lambda: proton)
    chatwoot.get_inbox.return_value = {"channel_type": "Channel::Email"}

    await lifecycle.on_conversation_created({"id": 43, "inbox_id": 2, "channel": "Channel::Email"})

    chatwoot.create_message.assert_awaited_once()
    args, kwargs = chatwoot.create_message.await_args
    assert args[1] == "Operator-edited ack body"


async def test_email_autoack_falls_back_to_env_when_store_returns_none(
    chatwoot, email_on, monkeypatch
):
    """An unset store value (None) means "not configured" — env text is used
    byte-identically, not an empty send."""
    proton = AsyncMock()
    proton.get_email_autoack_template.return_value = None
    monkeypatch.setattr(lifecycle, "get_proton_config_client", lambda: proton)
    chatwoot.get_inbox.return_value = {"channel_type": "Channel::Email"}

    await lifecycle.on_conversation_created({"id": 44, "inbox_id": 2, "channel": "Channel::Email"})

    chatwoot.create_message.assert_awaited_once()
    args, kwargs = chatwoot.create_message.await_args
    assert args[1] == lifecycle.get_settings().email_autoack_template


async def test_email_autoack_falls_back_to_env_when_proton_client_raises(
    chatwoot, email_on, monkeypatch
):
    """An unreachable backend must not stop the acknowledgement — fall back
    to env rather than sending nothing (fail-open, belt-and-suspenders on top
    of ProtonConfigClient's own internal fail-open)."""
    proton = AsyncMock()
    proton.get_email_autoack_template.side_effect = RuntimeError("unreachable")
    monkeypatch.setattr(lifecycle, "get_proton_config_client", lambda: proton)
    chatwoot.get_inbox.return_value = {"channel_type": "Channel::Email"}

    await lifecycle.on_conversation_created({"id": 45, "inbox_id": 2, "channel": "Channel::Email"})

    chatwoot.create_message.assert_awaited_once()
    args, kwargs = chatwoot.create_message.await_args
    assert args[1] == lifecycle.get_settings().email_autoack_template
