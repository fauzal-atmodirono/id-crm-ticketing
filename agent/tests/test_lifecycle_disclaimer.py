from unittest.mock import AsyncMock

import pytest

from app.services import lifecycle, lifecycle_store


@pytest.fixture
def chatwoot(monkeypatch):
    client = AsyncMock()
    monkeypatch.setattr(lifecycle, "get_chatwoot_client", lambda: client)
    # No proton backend configured → disclaimer falls back to the default.
    monkeypatch.setattr(lifecycle, "get_proton_config_client", lambda: None)
    return client


@pytest.fixture(autouse=True)
def _enable(monkeypatch):
    from app.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "lifecycle_disclaimer_enabled", True, raising=False)


async def test_on_conversation_created_seeds_and_posts_disclaimer(chatwoot):
    payload = {"id": 55, "inbox_id": 3, "channel": "Channel::Whatsapp"}
    await lifecycle.on_conversation_created(payload)

    assert await lifecycle_store.get_state(55) == "active"
    chatwoot.create_message.assert_awaited_once()
    args, kwargs = chatwoot.create_message.await_args
    assert args[0] == 55
    assert kwargs.get("private") is False


async def test_on_conversation_created_missing_id_is_noop(chatwoot):
    await lifecycle.on_conversation_created({"inbox_id": 3})
    chatwoot.create_message.assert_not_awaited()
