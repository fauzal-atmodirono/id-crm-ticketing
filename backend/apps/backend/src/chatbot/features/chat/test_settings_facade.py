"""Unit tests for settings_facade: type validation and effective-settings resolution."""

from __future__ import annotations

import pytest

from chatbot.features.chat.adapters.tenant_settings_store import InMemoryTenantSettingsStore
from chatbot.features.chat.settings_facade import (
    get_effective_settings,
    get_effective_value,
    validate_overrides,
)
from chatbot.platform.config import Settings


def _settings(**kw) -> Settings:
    base: dict = {
        "faq_admin_api_key": "fk",
        "proton_backend_key": "pk",
    }
    base.update(kw)
    return Settings(**base)


# ---------------------------------------------------------------------------
# validate_overrides — type coercion
# ---------------------------------------------------------------------------


def test_validate_model_coerces_to_str() -> None:
    out = validate_overrides({"assist_gemini_model": "gemini-2.5-pro"})
    assert out == {"assist_gemini_model": "gemini-2.5-pro"}


def test_validate_max_tool_iterations_coerces_int() -> None:
    out = validate_overrides({"copilot_max_tool_iterations": "10"})
    assert out == {"copilot_max_tool_iterations": 10}


def test_validate_max_tool_iterations_bad_type_raises() -> None:
    with pytest.raises(ValueError, match="copilot_max_tool_iterations"):
        validate_overrides({"copilot_max_tool_iterations": "not-a-number"})


def test_validate_bool_true_variants() -> None:
    for v in (True, "true", "1", "yes"):
        out = validate_overrides({"feature_ai_assist": v})
        assert out["feature_ai_assist"] is True


def test_validate_bool_false_variants() -> None:
    for v in (False, "false", "0", "no"):
        out = validate_overrides({"feature_copilot": v})
        assert out["feature_copilot"] is False


def test_validate_bool_bad_string_raises() -> None:
    with pytest.raises(ValueError, match="feature_drafts"):
        validate_overrides({"feature_drafts": "maybe"})


def test_validate_default_mode_valid() -> None:
    assert validate_overrides({"default_mode": "auto"}) == {"default_mode": "auto"}
    assert validate_overrides({"default_mode": "suggest"}) == {"default_mode": "suggest"}


def test_validate_default_mode_invalid_raises() -> None:
    with pytest.raises(ValueError, match="default_mode"):
        validate_overrides({"default_mode": "unknown"})


def test_validate_debounce_seconds_coerces_float() -> None:
    out = validate_overrides({"debounce_seconds": "2.5"})
    assert out == {"debounce_seconds": 2.5}


def test_validate_debounce_seconds_bad_raises() -> None:
    with pytest.raises(ValueError, match="debounce_seconds"):
        validate_overrides({"debounce_seconds": "fast"})


def test_validate_unknown_keys_ignored() -> None:
    out = validate_overrides({"unknown_key": "value", "assist_gemini_model": "m"})
    assert "unknown_key" not in out
    assert "assist_gemini_model" in out


def test_validate_clear_values_pass_through() -> None:
    out = validate_overrides({"assist_gemini_model": None, "feature_copilot": ""})
    assert out["assist_gemini_model"] is None
    assert out["feature_copilot"] == ""


# ---------------------------------------------------------------------------
# get_effective_settings — resolution: override > env
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_all_keys_present_with_env_source_when_store_empty() -> None:
    store = InMemoryTenantSettingsStore()
    s = _settings()
    result = await get_effective_settings(store, s)

    expected_keys = {
        "assist_gemini_model",
        "copilot_gemini_model",
        "copilot_max_tool_iterations",
        "feature_ai_assist",
        "feature_copilot",
        "feature_drafts",
        "default_mode",
        "debounce_seconds",
    }
    assert set(result.keys()) == expected_keys
    for key in expected_keys:
        assert result[key]["source"] == "env", f"{key} should report source=env"


@pytest.mark.anyio
async def test_env_defaults_match_settings_fields() -> None:
    store = InMemoryTenantSettingsStore()
    s = _settings(assist_gemini_model="gemini-pro", copilot_max_tool_iterations=3)
    result = await get_effective_settings(store, s)

    assert result["assist_gemini_model"]["value"] == "gemini-pro"
    assert result["copilot_max_tool_iterations"]["value"] == 3
    assert result["feature_ai_assist"]["value"] is False
    assert result["default_mode"]["value"] == "suggest"
    assert result["debounce_seconds"]["value"] == 0


@pytest.mark.anyio
async def test_override_replaces_env_and_reports_source_override() -> None:
    store = InMemoryTenantSettingsStore()
    s = _settings()
    await store.set_overrides({"assist_gemini_model": "gemini-2.5-pro", "feature_ai_assist": True})

    result = await get_effective_settings(store, s)

    assert result["assist_gemini_model"] == {"value": "gemini-2.5-pro", "source": "override"}
    assert result["feature_ai_assist"] == {"value": True, "source": "override"}
    # unset keys still report env
    assert result["default_mode"]["source"] == "env"


@pytest.mark.anyio
async def test_clear_override_reverts_to_env() -> None:
    store = InMemoryTenantSettingsStore()
    s = _settings()
    await store.set_overrides({"default_mode": "auto"})
    assert (await get_effective_settings(store, s))["default_mode"]["source"] == "override"

    await store.set_overrides({"default_mode": None})
    result = await get_effective_settings(store, s)
    assert result["default_mode"] == {"value": "suggest", "source": "env"}


@pytest.mark.anyio
async def test_get_effective_value_returns_resolved_value() -> None:
    store = InMemoryTenantSettingsStore()
    s = _settings(copilot_max_tool_iterations=7)
    await store.set_overrides({"copilot_max_tool_iterations": 12})
    val = await get_effective_value(store, s, "copilot_max_tool_iterations")
    assert val == 12


@pytest.mark.anyio
async def test_get_effective_value_unknown_key_raises() -> None:
    store = InMemoryTenantSettingsStore()
    s = _settings()
    with pytest.raises(KeyError, match="no_such_key"):
        await get_effective_value(store, s, "no_such_key")
