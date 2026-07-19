"""Tests for the custom-tool registry router (/kb/tools*).

Covers:
- x-api-key auth on every endpoint
- GET builtins shape + custom shape
- POST create (happy path, secret masking, auto-slug)
- POST dup slug → 409
- POST non-HTTPS endpoint_url → 422
- POST bad param_schema (missing "type") → 422
- POST cap > 15 → 409
- PUT update (partial, validates endpoint/schema)
- DELETE (happy + 404)
- PUT /kb/tools/builtins/{name} override toggle + description
- 404s for missing slug / unknown builtin name
- auth_config NEVER appears in any response
"""

from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.chat.adapters.tools_store import InMemoryToolsStore
from chatbot.features.chat.kb_tools_router import build_kb_tools_router
from chatbot.platform.config import Settings

_KEY = "test-admin-key"

_VALID_TOOL = {
    "title": "Check Order Status",
    "description": "Look up order by ID",
    "endpoint_url": "https://api.example.com/orders",
    "http_method": "GET",
    "auth_type": "bearer",
    "auth_config": {"token": "super-secret-bearer-token"},
    "param_schema": {"type": "object", "properties": {"order_id": {"type": "string"}}},
}


def _make_client(store: InMemoryToolsStore | None = None) -> TestClient:
    s = store or InMemoryToolsStore()
    settings = Settings(faq_admin_api_key=_KEY)
    app = FastAPI()
    app.include_router(build_kb_tools_router(s, settings))
    return TestClient(app, raise_server_exceptions=False)


def _make_client_with_store() -> tuple[TestClient, InMemoryToolsStore]:
    store = InMemoryToolsStore()
    return _make_client(store), store


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_get_requires_auth() -> None:
    c = _make_client()
    assert c.get("/kb/tools").status_code == 401


def test_post_requires_auth() -> None:
    c = _make_client()
    assert c.post("/kb/tools", json=_VALID_TOOL).status_code == 401


def test_put_requires_auth() -> None:
    c = _make_client()
    assert c.put("/kb/tools/some_slug", json={"title": "X"}).status_code == 401


def test_delete_requires_auth() -> None:
    c = _make_client()
    assert c.delete("/kb/tools/some_slug").status_code == 401


def test_builtin_override_requires_auth() -> None:
    c = _make_client()
    assert c.put("/kb/tools/builtins/search_knowledge_base", json={"enabled": False}).status_code == 401


def test_wrong_key_rejected() -> None:
    c = _make_client()
    assert c.get("/kb/tools", headers={"x-api-key": "wrong"}).status_code == 401


def test_proton_backend_key_accepted() -> None:
    store = InMemoryToolsStore()
    settings = Settings(proton_backend_key="pk", faq_admin_api_key="fk")
    app = FastAPI()
    app.include_router(build_kb_tools_router(store, settings))
    c = TestClient(app)
    r = c.get("/kb/tools", headers={"x-api-key": "pk"})
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# GET /kb/tools — builtins shape
# ---------------------------------------------------------------------------


def test_get_returns_builtins_list() -> None:
    c = _make_client()
    r = c.get("/kb/tools", headers={"x-api-key": _KEY})
    assert r.status_code == 200
    body = r.json()
    assert "builtins" in body
    assert "custom" in body
    # All built-in tool names must be present.
    names = {b["name"] for b in body["builtins"]}
    assert "search_knowledge_base" in names
    assert "get_conversation_transcript" in names


def test_builtin_entry_has_required_fields() -> None:
    c = _make_client()
    r = c.get("/kb/tools", headers={"x-api-key": _KEY})
    for builtin in r.json()["builtins"]:
        assert "name" in builtin
        assert "enabled" in builtin
        assert "description" in builtin


def test_builtins_default_enabled_true() -> None:
    c = _make_client()
    r = c.get("/kb/tools", headers={"x-api-key": _KEY})
    for builtin in r.json()["builtins"]:
        assert builtin["enabled"] is True


def test_custom_empty_initially() -> None:
    c = _make_client()
    r = c.get("/kb/tools", headers={"x-api-key": _KEY})
    assert r.json()["custom"] == []


# ---------------------------------------------------------------------------
# POST /kb/tools — create
# ---------------------------------------------------------------------------


def test_create_returns_slug() -> None:
    c, _store = _make_client_with_store()
    r = c.post("/kb/tools", json=_VALID_TOOL, headers={"x-api-key": _KEY})
    assert r.status_code == 200
    body = r.json()
    assert "slug" in body
    assert body["slug"].startswith("custom_")


def test_create_explicit_slug() -> None:
    c, _store = _make_client_with_store()
    tool = {**_VALID_TOOL, "slug": "custom_my_tool"}
    r = c.post("/kb/tools", json=tool, headers={"x-api-key": _KEY})
    assert r.status_code == 200
    assert r.json()["slug"] == "custom_my_tool"


def test_create_tool_appears_in_get() -> None:
    c, _ = _make_client_with_store()
    c.post("/kb/tools", json=_VALID_TOOL, headers={"x-api-key": _KEY})
    r = c.get("/kb/tools", headers={"x-api-key": _KEY})
    customs = r.json()["custom"]
    assert len(customs) == 1
    assert customs[0]["title"] == "Check Order Status"


# ---------------------------------------------------------------------------
# Secret masking — auth_config must NEVER appear in responses
# ---------------------------------------------------------------------------


def test_auth_config_not_in_get_response() -> None:
    c, _ = _make_client_with_store()
    c.post("/kb/tools", json=_VALID_TOOL, headers={"x-api-key": _KEY})
    r = c.get("/kb/tools", headers={"x-api-key": _KEY})
    for custom in r.json()["custom"]:
        assert "auth_config" not in custom
        assert "token" not in str(custom)


def test_auth_config_not_in_post_response() -> None:
    c, _ = _make_client_with_store()
    r = c.post("/kb/tools", json=_VALID_TOOL, headers={"x-api-key": _KEY})
    assert "auth_config" not in r.json()
    assert "super-secret" not in str(r.json())


def test_has_auth_flag_set_when_auth_config_present() -> None:
    c, _ = _make_client_with_store()
    c.post("/kb/tools", json=_VALID_TOOL, headers={"x-api-key": _KEY})
    r = c.get("/kb/tools", headers={"x-api-key": _KEY})
    custom = r.json()["custom"][0]
    assert custom["has_auth"] is True
    assert custom["auth_type"] == "bearer"


def test_has_auth_false_when_no_auth_config() -> None:
    c, _ = _make_client_with_store()
    tool = {**_VALID_TOOL, "auth_type": "none", "auth_config": {}}
    c.post("/kb/tools", json=tool, headers={"x-api-key": _KEY})
    r = c.get("/kb/tools", headers={"x-api-key": _KEY})
    custom = r.json()["custom"][0]
    assert custom["has_auth"] is False


# ---------------------------------------------------------------------------
# POST validation errors
# ---------------------------------------------------------------------------


def test_dup_slug_returns_409() -> None:
    c, _ = _make_client_with_store()
    tool = {**_VALID_TOOL, "slug": "custom_dup"}
    c.post("/kb/tools", json=tool, headers={"x-api-key": _KEY})
    r = c.post("/kb/tools", json=tool, headers={"x-api-key": _KEY})
    assert r.status_code == 409


def test_builtin_name_as_slug_returns_409() -> None:
    c, _ = _make_client_with_store()
    tool = {**_VALID_TOOL, "slug": "search_knowledge_base"}
    r = c.post("/kb/tools", json=tool, headers={"x-api-key": _KEY})
    assert r.status_code == 409


def test_non_https_endpoint_returns_422() -> None:
    c, _ = _make_client_with_store()
    tool = {**_VALID_TOOL, "endpoint_url": "http://insecure.example.com/api"}
    r = c.post("/kb/tools", json=tool, headers={"x-api-key": _KEY})
    assert r.status_code == 422


def test_http_not_accepted() -> None:
    c, _ = _make_client_with_store()
    tool = {**_VALID_TOOL, "endpoint_url": "HTTP://insecure.example.com/api"}
    r = c.post("/kb/tools", json=tool, headers={"x-api-key": _KEY})
    assert r.status_code == 422


def test_bad_param_schema_no_type_returns_422() -> None:
    c, _ = _make_client_with_store()
    tool = {**_VALID_TOOL, "param_schema": {"properties": {}}}
    r = c.post("/kb/tools", json=tool, headers={"x-api-key": _KEY})
    assert r.status_code == 422


def test_param_schema_with_type_accepted() -> None:
    c, _ = _make_client_with_store()
    tool = {**_VALID_TOOL, "param_schema": {"type": "object"}}
    r = c.post("/kb/tools", json=tool, headers={"x-api-key": _KEY})
    assert r.status_code == 200


def test_invalid_slug_format_returns_422() -> None:
    c, _ = _make_client_with_store()
    tool = {**_VALID_TOOL, "slug": "123invalid"}
    r = c.post("/kb/tools", json=tool, headers={"x-api-key": _KEY})
    assert r.status_code == 422


def test_slug_too_long_returns_422() -> None:
    c, _ = _make_client_with_store()
    tool = {**_VALID_TOOL, "slug": "a" * 65}
    r = c.post("/kb/tools", json=tool, headers={"x-api-key": _KEY})
    assert r.status_code == 422


def test_cap_15_returns_409() -> None:
    c, _ = _make_client_with_store()
    for i in range(15):
        r = c.post(
            "/kb/tools",
            json={**_VALID_TOOL, "title": f"Tool {i}"},
            headers={"x-api-key": _KEY},
        )
        assert r.status_code == 200, f"Expected 200 for tool {i}, got {r.status_code}: {r.text}"
    # 16th tool should be rejected.
    r = c.post("/kb/tools", json={**_VALID_TOOL, "title": "Tool 16"}, headers={"x-api-key": _KEY})
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# PUT /kb/tools/{slug}
# ---------------------------------------------------------------------------


def test_put_updates_tool() -> None:
    c, store = _make_client_with_store()
    slug_resp = c.post("/kb/tools", json=_VALID_TOOL, headers={"x-api-key": _KEY}).json()["slug"]

    r = c.put(
        f"/kb/tools/{slug_resp}",
        json={"description": "Updated description", "enabled": False},
        headers={"x-api-key": _KEY},
    )
    assert r.status_code == 200

    tool = await_get_custom(store, slug_resp)
    assert tool is not None
    assert tool.description == "Updated description"
    assert tool.enabled is False


def _sync_get(store: InMemoryToolsStore, slug: str) -> object:
    return asyncio.new_event_loop().run_until_complete(store.get_custom(slug))


await_get_custom = _sync_get


def test_put_nonexistent_slug_returns_404() -> None:
    c = _make_client()
    r = c.put("/kb/tools/no_such_slug", json={"title": "X"}, headers={"x-api-key": _KEY})
    assert r.status_code == 404


def test_put_non_https_endpoint_returns_422() -> None:
    c, _ = _make_client_with_store()
    slug = c.post("/kb/tools", json=_VALID_TOOL, headers={"x-api-key": _KEY}).json()["slug"]
    r = c.put(
        f"/kb/tools/{slug}",
        json={"endpoint_url": "http://bad.example.com"},
        headers={"x-api-key": _KEY},
    )
    assert r.status_code == 422


def test_put_bad_param_schema_returns_422() -> None:
    c, _ = _make_client_with_store()
    slug = c.post("/kb/tools", json=_VALID_TOOL, headers={"x-api-key": _KEY}).json()["slug"]
    r = c.put(
        f"/kb/tools/{slug}",
        json={"param_schema": {"properties": {}}},
        headers={"x-api-key": _KEY},
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# DELETE /kb/tools/{slug}
# ---------------------------------------------------------------------------


def test_delete_removes_tool() -> None:
    c, _store = _make_client_with_store()
    slug = c.post("/kb/tools", json=_VALID_TOOL, headers={"x-api-key": _KEY}).json()["slug"]

    r = c.delete(f"/kb/tools/{slug}", headers={"x-api-key": _KEY})
    assert r.status_code == 200

    r2 = c.get("/kb/tools", headers={"x-api-key": _KEY})
    assert r2.json()["custom"] == []


def test_delete_nonexistent_returns_404() -> None:
    c = _make_client()
    r = c.delete("/kb/tools/ghost_slug", headers={"x-api-key": _KEY})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# PUT /kb/tools/builtins/{name}
# ---------------------------------------------------------------------------


def test_builtin_override_disable() -> None:
    c = _make_client()
    r = c.put(
        "/kb/tools/builtins/search_knowledge_base",
        json={"enabled": False},
        headers={"x-api-key": _KEY},
    )
    assert r.status_code == 200

    # Reflect in GET response.
    r2 = c.get("/kb/tools", headers={"x-api-key": _KEY})
    builtins = {b["name"]: b for b in r2.json()["builtins"]}
    assert builtins["search_knowledge_base"]["enabled"] is False
    # Other builtins still enabled.
    assert builtins["get_conversation_transcript"]["enabled"] is True


def test_builtin_override_description() -> None:
    c = _make_client()
    c.put(
        "/kb/tools/builtins/get_contact_details",
        json={"description": "Custom tenant description"},
        headers={"x-api-key": _KEY},
    )
    r = c.get("/kb/tools", headers={"x-api-key": _KEY})
    builtins = {b["name"]: b for b in r.json()["builtins"]}
    assert builtins["get_contact_details"]["description"] == "Custom tenant description"


def test_builtin_override_unknown_name_returns_404() -> None:
    c = _make_client()
    r = c.put(
        "/kb/tools/builtins/nonexistent_tool",
        json={"enabled": False},
        headers={"x-api-key": _KEY},
    )
    assert r.status_code == 404


def test_builtin_override_partial_update_preserves_other_fields() -> None:
    """Setting only 'enabled' shouldn't wipe a previously set 'description'."""
    c = _make_client()
    c.put(
        "/kb/tools/builtins/search_knowledge_base",
        json={"description": "Custom description"},
        headers={"x-api-key": _KEY},
    )
    c.put(
        "/kb/tools/builtins/search_knowledge_base",
        json={"enabled": False},
        headers={"x-api-key": _KEY},
    )
    r = c.get("/kb/tools", headers={"x-api-key": _KEY})
    builtins = {b["name"]: b for b in r.json()["builtins"]}
    skb = builtins["search_knowledge_base"]
    assert skb["enabled"] is False
    assert skb["description"] == "Custom description"
