from unittest.mock import AsyncMock

import pytest

from app.services import categorize


@pytest.fixture
def chatwoot(monkeypatch):
    client = AsyncMock()
    client.get_messages.return_value = {"payload": [
        {"content": "my battery won't charge", "message_type": 0, "sender": {"name": "Cust"}},
    ]}
    monkeypatch.setattr(categorize, "get_chatwoot_client", lambda: client)
    return client


@pytest.fixture
def enabled(monkeypatch):
    s = categorize.get_settings()
    monkeypatch.setattr(s, "lifecycle_auto_categorize", True, raising=False)
    monkeypatch.setattr(s, "lifecycle_category_labels", "battery,sales", raising=False)


async def test_maybe_categorize_applies_label(chatwoot, enabled, monkeypatch):
    monkeypatch.setattr(categorize, "classify_category", AsyncMock(return_value="battery"))
    await categorize.maybe_categorize(77)
    chatwoot.add_labels.assert_awaited_once_with(77, ["category_battery"])


async def test_maybe_categorize_noop_when_disabled(chatwoot, monkeypatch):
    s = categorize.get_settings()
    monkeypatch.setattr(s, "lifecycle_auto_categorize", False, raising=False)
    await categorize.maybe_categorize(77)
    chatwoot.add_labels.assert_not_awaited()


async def test_maybe_categorize_noop_when_no_category(chatwoot, enabled, monkeypatch):
    monkeypatch.setattr(categorize, "classify_category", AsyncMock(return_value=None))
    await categorize.maybe_categorize(77)
    chatwoot.add_labels.assert_not_awaited()


async def test_maybe_categorize_failopen(chatwoot, enabled, monkeypatch):
    monkeypatch.setattr(categorize, "classify_category", AsyncMock(side_effect=RuntimeError("x")))
    # Must not raise.
    await categorize.maybe_categorize(77)
    chatwoot.add_labels.assert_not_awaited()
