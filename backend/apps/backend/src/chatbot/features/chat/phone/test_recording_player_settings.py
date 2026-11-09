"""The player's settings: safe defaults, the TTL clamp, and example.env."""

from __future__ import annotations

from pathlib import Path

import pytest

from chatbot.platform.config import Settings

_EXAMPLE_ENV = Path(__file__).resolve().parents[8] / "deploy" / "tenants" / "example.env"

NEW = (
    "CALL_RECORDING_SIGNING_SECRET",
    "CALL_RECORDING_URL_TTL_SECONDS",
    "CALL_RECORDING_RETRANSCRIBE_COOLDOWN_SECONDS",
)


def test_defaults_are_off_and_short() -> None:
    s = Settings()
    assert s.call_recording_retrieval_enabled is False
    assert s.call_recording_signing_secret == ""
    assert s.call_recording_url_ttl_seconds == 300
    assert s.call_recording_retranscribe_cooldown_seconds == 600


@pytest.mark.parametrize("raw,expected", [("3600", 300), ("5", 30), ("120", 120), ("junk", 300)])
def test_the_link_ttl_is_clamped_to_five_minutes(monkeypatch, raw, expected) -> None:
    monkeypatch.setenv("CALL_RECORDING_URL_TTL_SECONDS", raw)
    assert Settings().call_recording_url_ttl_seconds == expected


def test_every_new_setting_is_documented_in_example_env() -> None:
    text = _EXAMPLE_ENV.read_text(encoding="utf-8")
    for name in NEW:
        assert f"\n{name}=" in text, name
    # No real secret in the template.
    assert "\nCALL_RECORDING_SIGNING_SECRET=\n" in text
