"""P4 task 11 — the new settings, their defaults, and the env template.

Defaults are the deliverable here. Every P4 flag is off or identity-valued, so
an existing tenant that redeploys sees no change until somebody decides
otherwise -- and `REPORTING_TIMEZONE` in particular changes historical numbers
the moment it moves, so its default is load-bearing rather than cosmetic.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chatbot.platform.config import Settings

EXAMPLE_ENV = (
    Path(__file__).resolve().parents[7] / "deploy" / "tenants" / "example.env"
).read_text(encoding="utf-8")


def _settings() -> Settings:
    return Settings(_env_file=None)


def test_reporting_timezone_defaults_to_utc():
    assert _settings().reporting_timezone == "UTC"


def test_reopen_tracking_defaults_to_false_in_the_agent_env_template():
    assert "REOPEN_TRACKING_ENABLED=false" in EXAMPLE_ENV


@pytest.mark.parametrize(
    "key", ["REPORTING_TIMEZONE=UTC", "REOPEN_TRACKING_ENABLED=false"]
)
def test_every_new_setting_is_documented_in_example_env(key):
    assert key in EXAMPLE_ENV


def test_the_env_template_warns_that_changing_the_timezone_rebuckets_history():
    """The one setting whose default is not merely a preference."""
    assert "RE-BUCKETS EVERY HISTORICAL FIGURE" in EXAMPLE_ENV
    assert "compare-reporting-timezone.py" in EXAMPLE_ENV


def test_the_service_constructs_with_none_of_the_new_vars_set():
    settings = _settings()
    assert settings.reporting_timezone == "UTC"
    assert settings.email_blocked_recipients == ""


def test_an_unsupported_timezone_is_caught_before_any_view_is_created():
    from chatbot.features.metrics.bigquery_schema import view_ddls

    with pytest.raises(ValueError):
        view_ddls("p", "d", reporting_timezone="Mars/Olympus_Mons")


# --- P5 settings ----------------------------------------------------------


def test_the_p5_settings_are_documented_in_example_env():
    assert "CONTROL_ITEMS_ENABLED=false" in EXAMPLE_ENV
    assert "TARGETS_SEED_ENABLED=false" in EXAMPLE_ENV


def test_the_p5_settings_default_to_false():
    settings = _settings()
    assert settings.control_items_enabled is False
    assert settings.targets_seed_enabled is False


def test_the_env_template_states_that_blank_rows_are_not_zeros():
    """The sentence that stops someone 'tidying' the slide before a meeting."""
    assert "never zero" in EXAMPLE_ENV
    assert "claim about performance" in EXAMPLE_ENV
