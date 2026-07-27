"""Tests for GET/PUT /kb/inboxes/{id}/timing and timing embedded in list rows."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.chat.adapters.assistants_store import (
    Assistant,
    AssistantConfig,
    InMemoryAssistantsStore,
)
from chatbot.features.chat.adapters.inbox_assignment_store import InMemoryInboxAssignmentStore
from chatbot.features.chat.adapters.inbox_timing_store import InMemoryInboxTimingStore
from chatbot.features.chat.adapters.tenant_settings_store import InMemoryTenantSettingsStore
from chatbot.features.chat.kb_inboxes_router import build_kb_inboxes_router
from chatbot.platform.config import Settings

_KEY = "test-api-key"
_H = {"x-api-key": _KEY}
_ALL_NULL = {
    "idle_warn_minutes": None,
    "idle_close_grace_minutes": None,
    "idle_close_out_of_hours_grace_minutes": None,
    "confirm_grace_minutes": None,
    "idle_warning_message": None,
    "idle_close_message": None,
    "resolution_prompt_message": None,
    "assign_agent_message": None,
    "survey_ai_message": None,
    "survey_agent_message": None,
    "thanks_message": None,
    "inactivity_enabled": None,
}


def _settings() -> Settings:
    return Settings(faq_admin_api_key=_KEY, proton_backend_key=_KEY, chatwoot_enabled=False)


def _default_assistant() -> Assistant:
    return Assistant(
        id="asst_001", name="Anya", description="", product_name="",
        config=AssistantConfig(), enabled=True, is_default=True,
        created_at="2026-01-01T00:00:00+00:00",
    )


def _client(timing_store=None, chatwoot_inboxes=None):
    assistants = InMemoryAssistantsStore()
    assistants._assistants["asst_001"] = _default_assistant()  # seed default
    tenant = InMemoryTenantSettingsStore()
    assignments = InMemoryInboxAssignmentStore()
    timing = timing_store or InMemoryInboxTimingStore()
    cw = MagicMock()
    cw.list_inboxes = AsyncMock(return_value=chatwoot_inboxes or [])
    app = FastAPI()
    app.include_router(
        build_kb_inboxes_router(assignments, assistants, tenant, cw, _settings(), timing)
    )
    return TestClient(app), timing


def test_get_timing_unset_returns_all_null():
    client, _ = _client()
    r = client.get("/kb/inboxes/5/timing", headers=_H)
    assert r.status_code == 200
    assert r.json() == _ALL_NULL


def test_put_then_get_roundtrip():
    client, _ = _client()
    body = {"idle_warn_minutes": 12, "idle_close_grace_minutes": 3,
            "idle_close_out_of_hours_grace_minutes": 0, "confirm_grace_minutes": 8}
    r = client.put("/kb/inboxes/5/timing", json=body, headers=_H)
    assert r.status_code == 200
    assert r.json() == {**body, "idle_warning_message": None, "idle_close_message": None, "resolution_prompt_message": None, "assign_agent_message": None, "survey_ai_message": None, "survey_agent_message": None, "thanks_message": None, "inactivity_enabled": None}
    assert client.get("/kb/inboxes/5/timing", headers=_H).json() == {**body, "idle_warning_message": None, "idle_close_message": None, "resolution_prompt_message": None, "assign_agent_message": None, "survey_ai_message": None, "survey_agent_message": None, "thanks_message": None, "inactivity_enabled": None}


def test_put_null_field_is_unset():
    client, _ = _client()
    body = {"idle_warn_minutes": 15, "idle_close_grace_minutes": None,
            "idle_close_out_of_hours_grace_minutes": None, "confirm_grace_minutes": None}
    client.put("/kb/inboxes/5/timing", json=body, headers=_H)
    got = client.get("/kb/inboxes/5/timing", headers=_H).json()
    assert got == {**_ALL_NULL, "idle_warn_minutes": 15}


def test_put_all_null_deletes_doc():
    store = InMemoryInboxTimingStore()
    client, timing = _client(timing_store=store)
    client.put("/kb/inboxes/5/timing", json={"idle_warn_minutes": 9}, headers=_H)
    client.put("/kb/inboxes/5/timing", json=_ALL_NULL, headers=_H)
    assert store._data.get(5) is None


def test_put_out_of_range_is_422():
    client, _ = _client()
    r = client.put("/kb/inboxes/5/timing", json={"idle_warn_minutes": 5000}, headers=_H)
    assert r.status_code == 422
    r2 = client.put("/kb/inboxes/5/timing", json={"idle_warn_minutes": -1}, headers=_H)
    assert r2.status_code == 422


def test_timing_endpoints_require_auth():
    client, _ = _client()
    assert client.get("/kb/inboxes/5/timing").status_code == 401
    assert client.put("/kb/inboxes/5/timing", json=_ALL_NULL).status_code == 401


def test_list_rows_include_timing():
    store = InMemoryInboxTimingStore()
    client, _ = _client(
        timing_store=store,
        chatwoot_inboxes=[{"id": 5, "name": "WA", "channel_type": "Channel::Whatsapp"}],
    )
    client.put("/kb/inboxes/5/timing", json={"idle_warn_minutes": 7}, headers=_H)
    rows = client.get("/kb/inboxes", headers=_H).json()["inboxes"]
    row = next(r for r in rows if r["inbox_id"] == 5)
    assert row["idle_warn_minutes"] == 7
    assert row["confirm_grace_minutes"] is None


def test_put_get_message_and_enabled_roundtrip():
    client, _ = _client()
    body = {
        "idle_warn_minutes": None, "idle_close_grace_minutes": None,
        "idle_close_out_of_hours_grace_minutes": None, "confirm_grace_minutes": None,
        "idle_warning_message": "Closing in {{minutes}} min.",
        "inactivity_enabled": False,
    }
    r = client.put("/kb/inboxes/5/timing", json=body, headers=_H)
    assert r.status_code == 200
    got = client.get("/kb/inboxes/5/timing", headers=_H).json()
    assert got["idle_warning_message"] == "Closing in {{minutes}} min."
    assert got["inactivity_enabled"] is False
    # unset numbers still null
    assert got["idle_warn_minutes"] is None


def test_unset_message_and_enabled_are_null():
    client, _ = _client()
    got = client.get("/kb/inboxes/99/timing", headers=_H).json()
    assert got["idle_warning_message"] is None
    assert got["inactivity_enabled"] is None


def test_message_max_length_422():
    client, _ = _client()
    r = client.put("/kb/inboxes/5/timing", json={"idle_warning_message": "x" * 2001}, headers=_H)
    assert r.status_code == 422


def test_all_message_fields_roundtrip():
    client, _ = _client()
    body = {
        "idle_close_message": "Closed.", "resolution_prompt_message": "Resolved? Y/N",
        "assign_agent_message": "Assigning…", "survey_ai_message": "Rate AI",
        "survey_agent_message": "Rate agent", "thanks_message": "Ta",
    }
    assert client.put("/kb/inboxes/5/timing", json=body, headers=_H).status_code == 200
    got = client.get("/kb/inboxes/5/timing", headers=_H).json()
    for k, v in body.items():
        assert got[k] == v


def test_message_field_max_length_422():
    client, _ = _client()
    r = client.put("/kb/inboxes/5/timing", json={"thanks_message": "x" * 2001}, headers=_H)
    assert r.status_code == 422
