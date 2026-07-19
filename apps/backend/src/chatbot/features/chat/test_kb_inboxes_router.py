"""Tests for GET /kb/inboxes and PUT /kb/inboxes/{inbox_id}."""
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
from chatbot.features.chat.adapters.tenant_settings_store import InMemoryTenantSettingsStore
from chatbot.features.chat.kb_inboxes_router import build_kb_inboxes_router
from chatbot.platform.config import Settings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_KEY = "test-api-key"


def _settings(**kw) -> Settings:
    base = {
        "faq_admin_api_key": _KEY,
        "proton_backend_key": _KEY,
        "chatwoot_enabled": False,
    }
    base.update(kw)
    return Settings(**base)


def _make_assistant(asst_id: str = "asst_001", name: str = "Anya") -> Assistant:
    return Assistant(
        id=asst_id,
        name=name,
        description="",
        product_name="",
        config=AssistantConfig(),
        enabled=True,
        is_default=True,
        created_at="2026-01-01T00:00:00+00:00",
    )


def _mock_chatwoot(inboxes: list[dict] | None) -> MagicMock:
    """Return a mock ChatwootAdapter whose list_inboxes returns inboxes (or raises)."""
    m = MagicMock()
    if inboxes is None:
        m.list_inboxes = AsyncMock(side_effect=RuntimeError("chatwoot down"))
    else:
        m.list_inboxes = AsyncMock(return_value=inboxes)
    return m


def _build_stores(
    assignments: dict[int, tuple[str, str]] | None = None,
    assistants: list[Assistant] | None = None,
) -> tuple[InMemoryInboxAssignmentStore, InMemoryAssistantsStore, InMemoryTenantSettingsStore]:
    """Synchronously build pre-seeded stores.

    Uses the synchronous _data dict directly to avoid asyncio.get_event_loop()
    which is deprecated in Python 3.12 outside an event loop context.
    """
    assignment_store = InMemoryInboxAssignmentStore()
    if assignments:
        for iid, (asst_id, mode) in assignments.items():
            assignment_store._data[iid] = {"assistant_id": asst_id, "mode": mode}

    assistants_store = InMemoryAssistantsStore()
    if assistants:
        for a in assistants:
            assistants_store._assistants[a.id] = a

    tenant_store = InMemoryTenantSettingsStore()
    return assignment_store, assistants_store, tenant_store


def _build_client(
    chatwoot_inboxes: list[dict] | None = None,
    *,
    assignments: dict[int, tuple[str, str]] | None = None,
    assistants: list[Assistant] | None = None,
    settings_kw: dict | None = None,
) -> TestClient:
    settings = _settings(**(settings_kw or {}))
    assignment_store, assistants_store, tenant_store = _build_stores(assignments, assistants)
    chatwoot = _mock_chatwoot(chatwoot_inboxes)
    app = FastAPI()
    app.include_router(
        build_kb_inboxes_router(
            assignment_store, assistants_store, tenant_store, chatwoot, settings
        )
    )
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_missing_key_unauthorized() -> None:
    c = _build_client(chatwoot_inboxes=[])
    assert c.get("/kb/inboxes").status_code == 401


def test_wrong_key_unauthorized() -> None:
    c = _build_client(chatwoot_inboxes=[])
    assert c.get("/kb/inboxes", headers={"x-api-key": "wrong"}).status_code == 401


def test_valid_key_authorized() -> None:
    c = _build_client(chatwoot_inboxes=[])
    r = c.get("/kb/inboxes", headers={"x-api-key": _KEY})
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# GET /kb/inboxes — join logic
# ---------------------------------------------------------------------------


def test_get_empty_inboxes() -> None:
    c = _build_client(chatwoot_inboxes=[])
    r = c.get("/kb/inboxes", headers={"x-api-key": _KEY})
    assert r.status_code == 200
    assert r.json() == {"inboxes": []}


def test_get_inbox_default_source() -> None:
    """An inbox with no stored assignment gets source='default'."""
    asst = _make_assistant("asst_001", "Anya")
    c = _build_client(
        chatwoot_inboxes=[{"id": 1, "name": "Live Chat", "channel_type": "Channel::Api"}],
        assistants=[asst],
    )
    r = c.get("/kb/inboxes", headers={"x-api-key": _KEY})
    assert r.status_code == 200
    rows = r.json()["inboxes"]
    assert len(rows) == 1
    row = rows[0]
    assert row["inbox_id"] == 1
    assert row["name"] == "Live Chat"
    assert row["channel_type"] == "Channel::Api"
    assert row["source"] == "default"
    assert row["mode"] == "suggest"


def test_get_inbox_override_source() -> None:
    """An inbox with a stored assignment gets source='override'."""
    asst = _make_assistant("asst_001", "Anya")
    c = _build_client(
        chatwoot_inboxes=[{"id": 5, "name": "WhatsApp", "channel_type": "Channel::Whatsapp"}],
        assignments={5: ("asst_001", "auto")},
        assistants=[asst],
    )
    r = c.get("/kb/inboxes", headers={"x-api-key": _KEY})
    assert r.status_code == 200
    row = r.json()["inboxes"][0]
    assert row["inbox_id"] == 5
    assert row["source"] == "override"
    assert row["mode"] == "auto"
    assert row["assistant_id"] == "asst_001"
    assert row["assistant_name"] == "Anya"


def test_get_chatwoot_down_returns_stored_assignments() -> None:
    """When Chatwoot is unreachable, stored assignments are returned (degrade path)."""
    asst = _make_assistant("asst_001", "Anya")
    c = _build_client(
        chatwoot_inboxes=None,  # causes RuntimeError in list_inboxes
        assignments={7: ("asst_001", "off")},
        assistants=[asst],
    )
    r = c.get("/kb/inboxes", headers={"x-api-key": _KEY})
    assert r.status_code == 200
    rows = r.json()["inboxes"]
    assert len(rows) == 1
    assert rows[0]["inbox_id"] == 7
    assert rows[0]["source"] == "override"
    assert rows[0]["mode"] == "off"


def test_get_chatwoot_down_no_assignments_returns_empty() -> None:
    """Chatwoot down + no stored assignments → empty list, no 500."""
    c = _build_client(chatwoot_inboxes=None)
    r = c.get("/kb/inboxes", headers={"x-api-key": _KEY})
    assert r.status_code == 200
    assert r.json() == {"inboxes": []}


def test_get_assistant_name_best_effort_missing() -> None:
    """When the assigned assistant_id is not in the store, assistant_name is ''."""
    c = _build_client(
        chatwoot_inboxes=[{"id": 3, "name": "Email", "channel_type": "Channel::Email"}],
        assignments={3: ("asst_missing", "suggest")},
        # no assistants pre-created
    )
    r = c.get("/kb/inboxes", headers={"x-api-key": _KEY})
    assert r.status_code == 200
    row = r.json()["inboxes"][0]
    assert row["assistant_name"] == ""
    assert row["assistant_id"] == "asst_missing"


# ---------------------------------------------------------------------------
# PUT /kb/inboxes/{inbox_id}
# ---------------------------------------------------------------------------


def test_put_sets_assignment() -> None:
    asst = _make_assistant("asst_001", "Anya")
    c = _build_client(chatwoot_inboxes=[], assistants=[asst])
    r = c.put(
        "/kb/inboxes/10",
        json={"assistant_id": "asst_001", "mode": "suggest"},
        headers={"x-api-key": _KEY},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["inbox_id"] == 10
    assert body["assistant_id"] == "asst_001"
    assert body["mode"] == "suggest"


def test_put_invalid_mode_422() -> None:
    asst = _make_assistant("asst_001", "Anya")
    c = _build_client(chatwoot_inboxes=[], assistants=[asst])
    r = c.put(
        "/kb/inboxes/10",
        json={"assistant_id": "asst_001", "mode": "invalid"},
        headers={"x-api-key": _KEY},
    )
    assert r.status_code == 422


def test_put_unknown_assistant_404() -> None:
    c = _build_client(chatwoot_inboxes=[])
    r = c.put(
        "/kb/inboxes/10",
        json={"assistant_id": "asst_nonexistent", "mode": "auto"},
        headers={"x-api-key": _KEY},
    )
    assert r.status_code == 404


def test_put_null_assistant_id_clears() -> None:
    """Sending null assistant_id clears the assignment (reverts to default)."""
    asst = _make_assistant("asst_001", "Anya")
    c = _build_client(
        chatwoot_inboxes=[],
        assignments={10: ("asst_001", "off")},
        assistants=[asst],
    )
    r = c.put(
        "/kb/inboxes/10",
        json={"assistant_id": None, "mode": "suggest"},
        headers={"x-api-key": _KEY},
    )
    assert r.status_code == 200
    assert r.json()["cleared"] is True


def test_put_null_mode_clears() -> None:
    """Sending null mode clears the assignment."""
    asst = _make_assistant("asst_001", "Anya")
    c = _build_client(chatwoot_inboxes=[], assistants=[asst])
    r = c.put(
        "/kb/inboxes/10",
        json={"assistant_id": "asst_001", "mode": None},
        headers={"x-api-key": _KEY},
    )
    assert r.status_code == 200
    assert r.json()["cleared"] is True


def test_put_all_valid_modes() -> None:
    """All three valid modes are accepted."""
    asst = _make_assistant("asst_001", "Anya")
    c = _build_client(chatwoot_inboxes=[], assistants=[asst])
    for mode in ("off", "suggest", "auto"):
        r = c.put(
            "/kb/inboxes/10",
            json={"assistant_id": "asst_001", "mode": mode},
            headers={"x-api-key": _KEY},
        )
        assert r.status_code == 200, f"mode={mode} should be accepted"


def test_put_then_get_reflects_assignment() -> None:
    """PUT followed by GET returns the stored override."""
    asst = _make_assistant("asst_001", "Anya")

    settings_obj = _settings()
    assignment_store, assistants_store, tenant_store = _build_stores(assistants=[asst])
    chatwoot = _mock_chatwoot([{"id": 1, "name": "Chat", "channel_type": "Channel::Api"}])

    app = FastAPI()
    app.include_router(
        build_kb_inboxes_router(
            assignment_store, assistants_store, tenant_store, chatwoot, settings_obj
        )
    )
    c = TestClient(app, raise_server_exceptions=False)

    r = c.put(
        "/kb/inboxes/1",
        json={"assistant_id": "asst_001", "mode": "off"},
        headers={"x-api-key": _KEY},
    )
    assert r.status_code == 200

    r2 = c.get("/kb/inboxes", headers={"x-api-key": _KEY})
    rows = r2.json()["inboxes"]
    assert rows[0]["mode"] == "off"
    assert rows[0]["source"] == "override"


def test_put_auth_required() -> None:
    c = _build_client(chatwoot_inboxes=[])
    r = c.put("/kb/inboxes/1", json={"assistant_id": "x", "mode": "off"})
    assert r.status_code == 401
