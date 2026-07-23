"""Lifecycle settings default to the feature being off / no-op."""

from app.config import Settings


def _settings() -> Settings:
    # conftest.py has set all required env vars at import time.
    return Settings()


def test_lifecycle_defaults_are_off_and_safe():
    s = _settings()
    assert s.lifecycle_enabled is False
    assert s.lifecycle_scan_interval_seconds == 60
    assert s.lifecycle_idle_warn_minutes == 10
    assert s.lifecycle_idle_close_grace_minutes == 5
    assert s.lifecycle_idle_close_out_of_hours_grace_minutes == 0
    assert s.lifecycle_confirm_grace_minutes == 10
    assert s.lifecycle_survey_enabled is True
    assert s.lifecycle_disclaimer_enabled is True
    assert s.lifecycle_auto_categorize is False
