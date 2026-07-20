"""Effective-settings facade for tenant-level config overrides.

Resolves override → env for the eight editable tenant settings. Each resolved
key carries `{value, source}` where source is "override" when the store has a
non-None/non-empty value for that key, or "env" when falling back to the env
default.

The three model/iteration defaults come from the passed `Settings` object; the
remaining five are hardcoded as documented in the spec.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from chatbot.features.chat.adapters.tenant_settings_store import TenantSettingsStorePort
    from chatbot.platform.config import Settings

# Keys whose env default is NOT directly in Settings; hardcoded here per spec.
_HARDCODED_DEFAULTS: dict[str, Any] = {
    "feature_ai_assist": False,
    "feature_copilot": False,
    "feature_drafts": False,
    "default_mode": "suggest",
    "debounce_seconds": 0,
}

# All eight managed keys (ordered for consistent GET output)
_ALL_KEYS = [
    "assist_gemini_model",
    "copilot_gemini_model",
    "copilot_max_tool_iterations",
    "feature_ai_assist",
    "feature_copilot",
    "feature_drafts",
    "default_mode",
    "debounce_seconds",
]

_VALID_MODES = {"suggest", "auto"}

_BOOL_TRUE = {"true", "1", "yes"}
_BOOL_FALSE = {"false", "0", "no"}


def _coerce_bool(key: str, value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        if value.lower() in _BOOL_TRUE:
            return True
        if value.lower() in _BOOL_FALSE:
            return False
    raise ValueError(f"{key} must be a boolean, got {value!r}")


def _env_defaults(settings: Settings) -> dict[str, Any]:
    """Build the full env-default map, mixing Settings fields and hardcoded values."""
    return {
        "assist_gemini_model": settings.assist_gemini_model,
        "copilot_gemini_model": settings.copilot_gemini_model,
        "copilot_max_tool_iterations": settings.copilot_max_tool_iterations,
        **_HARDCODED_DEFAULTS,
    }


def _coerce_value(key: str, value: Any) -> Any:
    """Coerce a single override value to its expected Python type."""
    if key in ("assist_gemini_model", "copilot_gemini_model"):
        return str(value)
    if key == "copilot_max_tool_iterations":
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"copilot_max_tool_iterations must be an integer, got {value!r}"
            ) from exc
    if key in ("feature_ai_assist", "feature_copilot", "feature_drafts"):
        return _coerce_bool(key, value)
    if key == "default_mode":
        if str(value) not in _VALID_MODES:
            raise ValueError(
                f"default_mode must be one of {sorted(_VALID_MODES)}, got {value!r}"
            )
        return str(value)
    if key == "debounce_seconds":
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"debounce_seconds must be a number, got {value!r}"
            ) from exc
    return value  # unknown key — caller filters these out


def validate_overrides(partial: dict[str, Any]) -> dict[str, Any]:
    """Coerce and validate a partial override dict; raise ValueError on bad input.

    Unknown keys are silently ignored. None/"" values (clears) are passed through
    untouched because they are valid clear signals.
    """
    out: dict[str, Any] = {}
    for key, value in partial.items():
        if key not in _ALL_KEYS:
            continue  # ignore unknown keys
        if value is None or value == "":
            out[key] = value
            continue
        out[key] = _coerce_value(key, value)
    return out


async def get_effective_settings(
    store: TenantSettingsStorePort,
    settings: Settings,
) -> dict[str, dict[str, Any]]:
    """Return all eight keys as ``{key: {value, source}}`` dicts.

    Never raises — if the store fails it degrades to {} overrides and every key
    reports source="env".
    """
    try:
        overrides = await store.get_overrides()
    except Exception:
        overrides = {}

    defaults = _env_defaults(settings)
    result: dict[str, dict[str, Any]] = {}
    for key in _ALL_KEYS:
        override_val = overrides.get(key)
        if override_val is not None and override_val != "":
            result[key] = {"value": override_val, "source": "override"}
        else:
            result[key] = {"value": defaults[key], "source": "env"}
    return result


async def get_effective_value(
    store: TenantSettingsStorePort,
    settings: Settings,
    key: str,
) -> Any:
    """Return just the resolved value for a single key (used by copilot/assist routers)."""
    effective = await get_effective_settings(store, settings)
    if key not in effective:
        raise KeyError(f"Unknown settings key: {key!r}")
    return effective[key]["value"]
