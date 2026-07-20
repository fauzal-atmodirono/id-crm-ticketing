"""Integration tests for GET/PUT /kb/settings router."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatbot.features.chat.adapters.tenant_settings_store import InMemoryTenantSettingsStore
from chatbot.features.chat.kb_settings_router import build_kb_settings_router
from chatbot.platform.config import Settings


def _settings(**kw) -> Settings:
    base: dict = {
        "faq_admin_api_key": "fk",
        "proton_backend_key": "pk",
    }
    base.update(kw)
    return Settings(**base)


def _client(settings: Settings | None = None, store: InMemoryTenantSettingsStore | None = None) -> TestClient:
    s = settings or _settings()
    st = store or InMemoryTenantSettingsStore()
    app = FastAPI()
    app.include_router(build_kb_settings_router(st, s))
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_get_missing_key_401() -> None:
    c = _client()
    assert c.get("/kb/settings").status_code == 401


def test_get_wrong_key_401() -> None:
    c = _client()
    assert c.get("/kb/settings", headers={"x-api-key": "wrong"}).status_code == 401


def test_get_faq_key_200() -> None:
    c = _client()
    assert c.get("/kb/settings", headers={"x-api-key": "fk"}).status_code == 200


def test_get_proton_key_200() -> None:
    c = _client()
    assert c.get("/kb/settings", headers={"x-api-key": "pk"}).status_code == 200


def test_put_missing_key_401() -> None:
    c = _client()
    assert c.put("/kb/settings", json={}).status_code == 401


def test_put_wrong_key_401() -> None:
    c = _client()
    assert c.put("/kb/settings", json={}, headers={"x-api-key": "bad"}).status_code == 401


# ---------------------------------------------------------------------------
# GET — returns all keys with correct source when store is empty
# ---------------------------------------------------------------------------


def test_get_returns_all_eight_keys() -> None:
    c = _client()
    r = c.get("/kb/settings", headers={"x-api-key": "pk"})
    assert r.status_code == 200
    result = r.json()["settings"]
    expected = {
        "assist_gemini_model",
        "copilot_gemini_model",
        "copilot_max_tool_iterations",
        "feature_ai_assist",
        "feature_copilot",
        "feature_drafts",
        "default_mode",
        "debounce_seconds",
    }
    assert set(result.keys()) == expected


def test_get_all_keys_show_env_source_when_store_empty() -> None:
    c = _client()
    r = c.get("/kb/settings", headers={"x-api-key": "pk"})
    result = r.json()["settings"]
    for key, entry in result.items():
        assert entry["source"] == "env", f"{key} should report source=env"


def test_get_env_values_match_settings() -> None:
    s = _settings(assist_gemini_model="gemini-pro", copilot_max_tool_iterations=3)
    c = _client(settings=s)
    r = c.get("/kb/settings", headers={"x-api-key": "pk"})
    result = r.json()["settings"]

    assert result["assist_gemini_model"]["value"] == "gemini-pro"
    assert result["copilot_max_tool_iterations"]["value"] == 3
    assert result["feature_ai_assist"]["value"] is False
    assert result["default_mode"]["value"] == "suggest"
    assert result["debounce_seconds"]["value"] == 0


# ---------------------------------------------------------------------------
# PUT — persists override → GET shows source=override
# ---------------------------------------------------------------------------


def test_put_override_reflected_in_get() -> None:
    store = InMemoryTenantSettingsStore()
    s = _settings()
    c = _client(settings=s, store=store)

    r = c.put(
        "/kb/settings",
        json={"assist_gemini_model": "gemini-2.5-pro", "feature_ai_assist": True},
        headers={"x-api-key": "pk"},
    )
    assert r.status_code == 200
    result = r.json()["settings"]
    assert result["assist_gemini_model"] == {"value": "gemini-2.5-pro", "source": "override"}
    assert result["feature_ai_assist"] == {"value": True, "source": "override"}
    # Unchanged keys still report env
    assert result["default_mode"]["source"] == "env"


def test_put_returns_fresh_effective_settings() -> None:
    store = InMemoryTenantSettingsStore()
    c = _client(store=store)

    r = c.put(
        "/kb/settings",
        json={"default_mode": "auto"},
        headers={"x-api-key": "pk"},
    )
    assert r.status_code == 200
    assert r.json()["settings"]["default_mode"]["value"] == "auto"


def test_get_after_put_shows_override() -> None:
    store = InMemoryTenantSettingsStore()
    s = _settings()
    c = _client(settings=s, store=store)

    c.put("/kb/settings", json={"debounce_seconds": 3.5}, headers={"x-api-key": "pk"})
    r = c.get("/kb/settings", headers={"x-api-key": "pk"})
    result = r.json()["settings"]
    assert result["debounce_seconds"] == {"value": 3.5, "source": "override"}


# ---------------------------------------------------------------------------
# PUT null/"" clears override → reverts to env
# ---------------------------------------------------------------------------


def test_put_null_clears_override() -> None:
    store = InMemoryTenantSettingsStore()
    s = _settings()
    c = _client(settings=s, store=store)

    c.put("/kb/settings", json={"default_mode": "auto"}, headers={"x-api-key": "pk"})
    c.put("/kb/settings", json={"default_mode": None}, headers={"x-api-key": "pk"})

    r = c.get("/kb/settings", headers={"x-api-key": "pk"})
    assert r.json()["settings"]["default_mode"] == {"value": "suggest", "source": "env"}


def test_put_empty_string_clears_override() -> None:
    store = InMemoryTenantSettingsStore()
    s = _settings()
    c = _client(settings=s, store=store)

    c.put("/kb/settings", json={"assist_gemini_model": "custom-model"}, headers={"x-api-key": "pk"})
    c.put("/kb/settings", json={"assist_gemini_model": ""}, headers={"x-api-key": "pk"})

    r = c.get("/kb/settings", headers={"x-api-key": "pk"})
    entry = r.json()["settings"]["assist_gemini_model"]
    assert entry["source"] == "env"


# ---------------------------------------------------------------------------
# PUT bad types → 422
# ---------------------------------------------------------------------------


def test_put_bad_int_returns_422() -> None:
    c = _client()
    r = c.put(
        "/kb/settings",
        json={"copilot_max_tool_iterations": "not-a-number"},
        headers={"x-api-key": "pk"},
    )
    assert r.status_code == 422


def test_put_bad_bool_returns_422() -> None:
    c = _client()
    r = c.put(
        "/kb/settings",
        json={"feature_drafts": "maybe"},
        headers={"x-api-key": "pk"},
    )
    assert r.status_code == 422


def test_put_bad_mode_returns_422() -> None:
    c = _client()
    r = c.put(
        "/kb/settings",
        json={"default_mode": "unknown_mode"},
        headers={"x-api-key": "pk"},
    )
    assert r.status_code == 422


def test_put_bad_debounce_returns_422() -> None:
    c = _client()
    r = c.put(
        "/kb/settings",
        json={"debounce_seconds": "very-fast"},
        headers={"x-api-key": "pk"},
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Unknown keys in PUT body are ignored (not 422)
# ---------------------------------------------------------------------------


def test_put_unknown_keys_ignored() -> None:
    c = _client()
    r = c.put(
        "/kb/settings",
        json={"some_unknown_key": "value", "default_mode": "auto"},
        headers={"x-api-key": "pk"},
    )
    assert r.status_code == 200
    result = r.json()["settings"]
    assert "some_unknown_key" not in result
    assert result["default_mode"]["value"] == "auto"
