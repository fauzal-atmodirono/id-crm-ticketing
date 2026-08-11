"""DMS/TSP integration shell settings.

`DMS_MOCK_CLIENT_ENABLED` is the single flag between fabricated demo records
and a real customer's Customer 360 panel: Phase 1 ships no real DMS adapter,
so it is the only thing that can put anything at all in the `dms` block. It
used to be a raw `os.getenv` in main.py -- not a Settings field, not in
`deploy/tenants/example.env`, and untested -- which made it invisible to
anyone auditing a tenant's env and hard to find for anyone who needed to turn
it off.
"""

import os

from chatbot.platform.config import Settings


def test_dms_mock_client_is_off_by_default() -> None:
    assert Settings().dms_mock_client_enabled is False


def test_the_environment_defaults_to_production_which_is_the_refusing_value(monkeypatch) -> None:
    """The safe default is the one that REFUSES the mock.

    `app_environment` exists only to make `MockDmsClient`'s sandbox guard able
    to fire; the guard was unreachable because the argument defaulted to
    `"sandbox"`. A setting defaulting to the permissive value would reproduce
    exactly that, so the default has to be checked, and checked properly: a bare
    `Settings()` reads `os.environ`, so the var is deleted first and the delete
    is asserted (see DISPATCH-RULES, "the `Settings(_env_file=None)` trap").
    """
    monkeypatch.delenv("APP_ENVIRONMENT", raising=False)
    assert "APP_ENVIRONMENT" not in os.environ
    assert Settings().app_environment == "production"


def test_the_environment_is_read_from_the_documented_env_var(monkeypatch) -> None:
    """Pins the field to `APP_ENVIRONMENT` verbatim. A rename on either side
    would leave the setting stuck at "production" -- safe, but silently
    un-settable, so a demo tenant could never enable the mock and would have no
    way to tell why.
    """
    monkeypatch.setenv("APP_ENVIRONMENT", "sandbox")
    assert Settings().app_environment == "sandbox"


def test_dms_mock_client_can_be_enabled_by_env(monkeypatch) -> None:
    """Pins that the field maps to the documented env var name verbatim --
    a rename on either side would silently leave the flag stuck off (or, if
    the default ever flipped, stuck on)."""
    monkeypatch.setenv("DMS_MOCK_CLIENT_ENABLED", "true")
    assert Settings().dms_mock_client_enabled is True
