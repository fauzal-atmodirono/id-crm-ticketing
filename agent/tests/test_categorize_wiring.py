from unittest.mock import AsyncMock

import pytest

from app.services import categorize


@pytest.fixture
def chatwoot(monkeypatch):
    client = AsyncMock()
    client.get_conversation.return_value = {"id": 77, "custom_attributes": {}}
    client.get_messages.return_value = {"payload": [
        {"content": "my battery won't charge", "message_type": 0, "sender": {"name": "Cust"}},
    ]}
    monkeypatch.setattr(categorize, "get_chatwoot_client", lambda: client)
    return client


@pytest.fixture
def enabled(monkeypatch):
    s = categorize.get_settings()
    monkeypatch.setattr(s, "lifecycle_auto_categorize", True, raising=False)
    monkeypatch.setattr(
        s,
        "case_taxonomy_json",
        '{"battery": {"label": "Battery"}, "sales": {"label": "Sales"}}',
        raising=False,
    )
    # These tests predate Task 5 and only exercise case_category; blank out
    # the option lists so the real default option lists don't trigger the
    # case_type/vehicle_model fallback classification unexpectedly.
    monkeypatch.setattr(s, "case_type_options_json", "", raising=False)
    monkeypatch.setattr(s, "vehicle_models_json", "", raising=False)
    return s


async def test_maybe_categorize_sets_custom_attribute(chatwoot, enabled, monkeypatch):
    monkeypatch.setattr(categorize, "classify_category", AsyncMock(return_value="battery"))
    await categorize.maybe_categorize(77)
    chatwoot.set_custom_attributes.assert_awaited_once_with(77, {"case_category": "Battery"})


async def test_maybe_categorize_sets_subcategory_when_taxonomy_defines_one(
    chatwoot, monkeypatch
):
    s = categorize.get_settings()
    monkeypatch.setattr(s, "lifecycle_auto_categorize", True, raising=False)
    monkeypatch.setattr(
        s,
        "case_taxonomy_json",
        '{"sales": {"label": "Sales", "subcategories": ["Test Drive Booking"]}}',
        raising=False,
    )

    async def fake_classify(transcript, candidates):
        if "sales" in candidates:
            return "sales"
        if "Test Drive Booking" in candidates:
            return "Test Drive Booking"
        return None

    monkeypatch.setattr(categorize, "classify_category", fake_classify)
    await categorize.maybe_categorize(77)
    chatwoot.set_custom_attributes.assert_awaited_once_with(
        77, {"case_category": "Sales", "case_subcategory": "Sales: Test Drive Booking"}
    )


async def test_maybe_categorize_omits_subcategory_when_no_match(chatwoot, monkeypatch):
    s = categorize.get_settings()
    monkeypatch.setattr(s, "lifecycle_auto_categorize", True, raising=False)
    monkeypatch.setattr(
        s,
        "case_taxonomy_json",
        '{"sales": {"label": "Sales", "subcategories": ["Test Drive Booking"]}}',
        raising=False,
    )

    async def fake_classify(transcript, candidates):
        return "sales" if "sales" in candidates else None

    monkeypatch.setattr(categorize, "classify_category", fake_classify)
    await categorize.maybe_categorize(77)
    chatwoot.set_custom_attributes.assert_awaited_once_with(77, {"case_category": "Sales"})


async def test_maybe_categorize_skips_when_already_classified(chatwoot, enabled):
    chatwoot.get_conversation.return_value = {
        "id": 77,
        "custom_attributes": {"case_category": "sales"},
    }
    await categorize.maybe_categorize(77)
    chatwoot.get_messages.assert_not_awaited()
    chatwoot.set_custom_attributes.assert_not_awaited()


async def test_maybe_categorize_noop_when_disabled(chatwoot, monkeypatch):
    s = categorize.get_settings()
    monkeypatch.setattr(s, "lifecycle_auto_categorize", False, raising=False)
    await categorize.maybe_categorize(77)
    chatwoot.get_conversation.assert_not_awaited()
    chatwoot.get_messages.assert_not_awaited()
    chatwoot.set_custom_attributes.assert_not_awaited()


async def test_maybe_categorize_noop_when_taxonomy_empty(chatwoot, monkeypatch):
    s = categorize.get_settings()
    monkeypatch.setattr(s, "lifecycle_auto_categorize", True, raising=False)
    monkeypatch.setattr(s, "case_taxonomy_json", "", raising=False)
    await categorize.maybe_categorize(77)
    chatwoot.get_conversation.assert_not_awaited()
    chatwoot.set_custom_attributes.assert_not_awaited()


async def test_maybe_categorize_noop_when_no_category(chatwoot, enabled, monkeypatch):
    monkeypatch.setattr(categorize, "classify_category", AsyncMock(return_value=None))
    await categorize.maybe_categorize(77)
    chatwoot.set_custom_attributes.assert_not_awaited()


async def test_maybe_categorize_noop_when_no_transcript(chatwoot, enabled):
    chatwoot.get_messages.return_value = {"payload": []}
    await categorize.maybe_categorize(77)
    chatwoot.set_custom_attributes.assert_not_awaited()


async def test_maybe_categorize_failopen(chatwoot, enabled, monkeypatch):
    monkeypatch.setattr(categorize, "classify_category", AsyncMock(side_effect=RuntimeError("x")))
    # Must not raise.
    await categorize.maybe_categorize(77)
    chatwoot.set_custom_attributes.assert_not_awaited()


async def test_maybe_categorize_failopen_on_get_conversation_error(chatwoot, enabled):
    chatwoot.get_conversation.side_effect = RuntimeError("boom")
    # Must not raise.
    await categorize.maybe_categorize(77)
    chatwoot.set_custom_attributes.assert_not_awaited()


async def test_maybe_categorize_also_classifies_case_type_and_vehicle_model(monkeypatch):
    # Task 5: after picking case_category, maybe_categorize best-effort
    # classifies case_type/vehicle_model too, using the same classify_category
    # helper against the tenant's configured option lists.
    from app.config import get_settings

    injected_client = AsyncMock()
    injected_client.get_conversation.return_value = {"id": 3, "custom_attributes": {}}
    injected_client.get_messages.return_value = {
        "payload": [
            {
                "content": "I want to book a test drive for the e.MAS 7",
                "sender": {"type": "contact"},
            }
        ]
    }
    injected_settings = get_settings().model_copy(
        update={
            "lifecycle_auto_categorize": True,
            "case_taxonomy_json": '{"sales": {"label": "Sales", "subcategories": []}}',
            "case_type_options_json": '{"options": ["Inquiry", "Complaint"]}',
            "vehicle_models_json": '{"options": ["e.MAS 5", "e.MAS 7"]}',
        }
    )

    async def fake_classify(transcript, candidates):
        if candidates == ["sales"]:
            return "sales"
        if "Inquiry" in candidates:
            return "Inquiry"
        if "e.MAS 7" in candidates:
            return "e.MAS 7"
        return None

    monkeypatch.setattr(categorize, "classify_category", fake_classify)
    await categorize.maybe_categorize(3, settings=injected_settings, chatwoot=injected_client)
    injected_client.set_custom_attributes.assert_awaited_once_with(
        3,
        {
            "case_category": "Sales",
            "case_type": "Inquiry",
            "vehicle_model": "e.MAS 7",
        },
    )


async def test_maybe_categorize_skips_case_type_when_already_set(monkeypatch):
    # Task 5: an already-set case_type (e.g. set mid-conversation by backend/'s
    # live classifier) must never be overwritten by this fallback.
    from app.config import get_settings

    injected_client = AsyncMock()
    injected_client.get_conversation.return_value = {
        "id": 4,
        "custom_attributes": {"case_type": "Complaint"},
    }
    injected_client.get_messages.return_value = {
        "payload": [{"content": "hello", "sender": {"type": "contact"}}]
    }
    injected_settings = get_settings().model_copy(
        update={
            "lifecycle_auto_categorize": True,
            "case_taxonomy_json": '{"sales": {"label": "Sales", "subcategories": []}}',
            "case_type_options_json": '{"options": ["Inquiry", "Complaint"]}',
        }
    )
    monkeypatch.setattr(categorize, "classify_category", AsyncMock(return_value="sales"))
    await categorize.maybe_categorize(4, settings=injected_settings, chatwoot=injected_client)
    written = injected_client.set_custom_attributes.await_args.args[1]
    assert "case_type" not in written


async def test_maybe_categorize_accepts_injected_settings_and_chatwoot(monkeypatch):
    # Task 6: maybe_categorize accepts optional settings/chatwoot for DI in
    # tests, independent of the module-level get_settings()/get_chatwoot_client()
    # singletons used by production callers.
    from app.config import get_settings

    injected_client = AsyncMock()
    injected_client.get_conversation.return_value = {"id": 1, "custom_attributes": {}}
    injected_client.get_messages.return_value = {
        "payload": [{"content": "hi", "sender": {"type": "contact"}}]
    }
    injected_settings = get_settings().model_copy(
        update={
            "lifecycle_auto_categorize": True,
            "case_taxonomy_json": '{"sales": {"label": "Sales", "subcategories": []}}',
            # This test predates Task 5 and only exercises case_category;
            # blank out the option lists so the real default option lists
            # don't trigger the case_type/vehicle_model fallback.
            "case_type_options_json": "",
            "vehicle_models_json": "",
        }
    )
    monkeypatch.setattr(categorize, "classify_category", AsyncMock(return_value="sales"))
    await categorize.maybe_categorize(1, settings=injected_settings, chatwoot=injected_client)
    injected_client.set_custom_attributes.assert_awaited_once_with(1, {"case_category": "Sales"})
