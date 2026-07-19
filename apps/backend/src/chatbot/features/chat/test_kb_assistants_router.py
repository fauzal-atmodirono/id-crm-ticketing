"""Tests for the KB Assistants CRUD router.

Uses InMemoryAssistantsStore to exercise the router without Firestore.
Follows the same pattern as test_kb_documents_router + test_faq_admin_auth.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.assist.copilot_tools import COPILOT_TOOLS
from chatbot.features.chat.adapters.assistants_store import (
    InMemoryAssistantsStore,
)
from chatbot.features.chat.kb_assistants_router import build_kb_assistants_router
from chatbot.platform.config import Settings


def _settings(**kw: object) -> Settings:
    base: dict[str, object] = {
        "faq_admin_api_key": "fk",
        "proton_backend_key": "pk",
    }
    base.update(kw)
    return Settings(**base)  # type: ignore[arg-type]


def _client(settings: Settings | None = None) -> tuple[TestClient, InMemoryAssistantsStore]:
    s = settings or _settings()
    store = InMemoryAssistantsStore()
    app = FastAPI()
    app.include_router(build_kb_assistants_router(store, s))
    return TestClient(app, raise_server_exceptions=False), store


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_no_key_rejected() -> None:
    c, _ = _client()
    assert c.get("/kb/assistants").status_code == 401


def test_wrong_key_rejected() -> None:
    c, _ = _client()
    assert c.get("/kb/assistants", headers={"x-api-key": "nope"}).status_code == 401


def test_proton_key_accepted() -> None:
    c, _ = _client()
    r = c.get("/kb/assistants", headers={"x-api-key": "pk"})
    assert r.status_code == 200


def test_faq_key_accepted() -> None:
    c, _ = _client()
    r = c.get("/kb/assistants", headers={"x-api-key": "fk"})
    assert r.status_code == 200


def test_empty_keys_reject_everything() -> None:
    s = _settings(faq_admin_api_key="", proton_backend_key="")
    c, _ = _client(s)
    assert c.get("/kb/assistants", headers={"x-api-key": ""}).status_code == 401


# ---------------------------------------------------------------------------
# Default seed on first GET
# ---------------------------------------------------------------------------


def test_list_seeds_default_on_empty_store() -> None:
    """GET /kb/assistants on an empty store returns one default assistant."""
    c, _ = _client()
    r = c.get("/kb/assistants", headers={"x-api-key": "pk"})
    assert r.status_code == 200
    body = r.json()
    assert "assistants" in body
    assert len(body["assistants"]) == 1
    a = body["assistants"][0]
    assert a["is_default"] is True
    assert a["name"] == "Default Assistant"
    assert a["id"].startswith("asst_")


def test_default_assistant_has_all_builtin_tools() -> None:
    """Default assistant enables all COPILOT_TOOLS by default."""
    c, _ = _client()
    r = c.get("/kb/assistants", headers={"x-api-key": "pk"})
    default = r.json()["assistants"][0]
    tool_names = {t["name"] for t in COPILOT_TOOLS}
    enabled = set(default["config"]["enabled_builtin_tools"])
    assert tool_names == enabled


def test_default_seed_is_idempotent() -> None:
    """Calling GET twice does not create a second default assistant."""
    c, _ = _client()
    headers = {"x-api-key": "pk"}
    r1 = c.get("/kb/assistants", headers=headers)
    r2 = c.get("/kb/assistants", headers=headers)
    assert len(r1.json()["assistants"]) == 1
    assert len(r2.json()["assistants"]) == 1
    assert r1.json()["assistants"][0]["id"] == r2.json()["assistants"][0]["id"]


# ---------------------------------------------------------------------------
# POST /kb/assistants
# ---------------------------------------------------------------------------


def test_create_assistant_returns_id() -> None:
    c, _ = _client()
    r = c.post(
        "/kb/assistants",
        json={"name": "Sales Bot", "description": "Sells stuff", "product_name": "PROTON"},
        headers={"x-api-key": "pk"},
    )
    assert r.status_code == 200
    body = r.json()
    assert "id" in body
    assert body["id"].startswith("asst_")


def test_create_assistant_appears_in_list() -> None:
    c, _ = _client()
    headers = {"x-api-key": "pk"}
    c.post(
        "/kb/assistants",
        json={"name": "Sales Bot", "description": "", "product_name": "PROTON"},
        headers=headers,
    )
    r = c.get("/kb/assistants", headers=headers)
    names = [a["name"] for a in r.json()["assistants"]]
    assert "Sales Bot" in names


def test_create_assistant_default_config() -> None:
    """Newly created assistant has sensible default config."""
    c, _ = _client()
    r = c.post(
        "/kb/assistants",
        json={"name": "New Bot", "description": "", "product_name": "P"},
        headers={"x-api-key": "pk"},
    )
    created_id = r.json()["id"]
    r2 = c.get(f"/kb/assistants/{created_id}", headers={"x-api-key": "pk"})
    cfg = r2.json()["config"]
    assert cfg["temperature"] == 1.0
    assert cfg["instructions"] == ""
    assert cfg["guardrails"] == []
    assert cfg["response_guidelines"] == []
    assert cfg["feature_faq"] is True


# ---------------------------------------------------------------------------
# GET /kb/assistants/{id}
# ---------------------------------------------------------------------------


def test_get_by_id() -> None:
    c, _ = _client()
    headers = {"x-api-key": "pk"}
    post_r = c.post(
        "/kb/assistants",
        json={"name": "Foo", "description": "", "product_name": "X"},
        headers=headers,
    )
    aid = post_r.json()["id"]
    r = c.get(f"/kb/assistants/{aid}", headers=headers)
    assert r.status_code == 200
    assert r.json()["id"] == aid
    assert r.json()["name"] == "Foo"


def test_get_missing_id_returns_404() -> None:
    c, _ = _client()
    r = c.get("/kb/assistants/asst_nonexistent", headers={"x-api-key": "pk"})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# PUT /kb/assistants/{id}
# ---------------------------------------------------------------------------


def test_put_updates_name() -> None:
    c, _ = _client()
    headers = {"x-api-key": "pk"}
    aid = c.post(
        "/kb/assistants",
        json={"name": "Old Name", "description": "", "product_name": "X"},
        headers=headers,
    ).json()["id"]
    c.put(f"/kb/assistants/{aid}", json={"name": "New Name"}, headers=headers)
    r = c.get(f"/kb/assistants/{aid}", headers=headers)
    assert r.json()["name"] == "New Name"


def test_put_updates_config_fields() -> None:
    c, _ = _client()
    headers = {"x-api-key": "pk"}
    aid = c.post(
        "/kb/assistants",
        json={"name": "Bot", "description": "", "product_name": "X"},
        headers=headers,
    ).json()["id"]
    c.put(
        f"/kb/assistants/{aid}",
        json={"config": {"temperature": 0.5, "instructions": "Be concise"}},
        headers=headers,
    )
    r = c.get(f"/kb/assistants/{aid}", headers=headers)
    cfg = r.json()["config"]
    assert cfg["temperature"] == 0.5
    assert cfg["instructions"] == "Be concise"


def test_put_missing_id_returns_404() -> None:
    c, _ = _client()
    r = c.put(
        "/kb/assistants/asst_missing",
        json={"name": "X"},
        headers={"x-api-key": "pk"},
    )
    assert r.status_code == 404


def test_put_persists_across_gets() -> None:
    """Updating an assistant persists; subsequent GETs see the updated value."""
    c, _ = _client()
    headers = {"x-api-key": "pk"}
    aid = c.post(
        "/kb/assistants",
        json={"name": "Bot", "description": "", "product_name": "X"},
        headers=headers,
    ).json()["id"]
    c.put(f"/kb/assistants/{aid}", json={"description": "updated desc"}, headers=headers)
    r1 = c.get(f"/kb/assistants/{aid}", headers=headers)
    r2 = c.get(f"/kb/assistants/{aid}", headers=headers)
    assert r1.json()["description"] == "updated desc"
    assert r2.json()["description"] == "updated desc"


# ---------------------------------------------------------------------------
# DELETE /kb/assistants/{id}
# ---------------------------------------------------------------------------


def test_delete_assistant() -> None:
    c, _ = _client()
    headers = {"x-api-key": "pk"}
    aid = c.post(
        "/kb/assistants",
        json={"name": "ToDelete", "description": "", "product_name": "X"},
        headers=headers,
    ).json()["id"]
    del_r = c.delete(f"/kb/assistants/{aid}", headers=headers)
    assert del_r.status_code == 200
    assert c.get(f"/kb/assistants/{aid}", headers=headers).status_code == 404


def test_delete_default_returns_409() -> None:
    """Deleting the default assistant returns 409."""
    c, _ = _client()
    headers = {"x-api-key": "pk"}
    # Seed the default by listing.
    list_r = c.get("/kb/assistants", headers=headers)
    default_id = list_r.json()["assistants"][0]["id"]
    r = c.delete(f"/kb/assistants/{default_id}", headers=headers)
    assert r.status_code == 409


def test_delete_nonexistent_returns_404() -> None:
    c, _ = _client()
    r = c.delete("/kb/assistants/asst_ghost", headers={"x-api-key": "pk"})
    assert r.status_code == 404
