"""Tests for per-channel first-response (ack) SLA config (Task 6).

Settings() is constructed with no overrides — all fields have defaults and
the suite runs with GOOGLE_API_KEY=dummy in the environment, which is the
only env var the google-genai SDK requires at import time. This mirrors the
pattern used in test_chatwoot_config.py and test_routing_config.py.
"""

from __future__ import annotations

from chatbot.platform.config import Settings


def test_ack_minutes_config_default_empty() -> None:
    # Construct with the minimum required env already provided by the test env.
    s = Settings()
    assert s.sla_ack_minutes_by_channel_json == ""


def test_ack_minutes_config_accepts_json_string() -> None:
    s = Settings(
        _env_file=None,
        sla_ack_minutes_by_channel_json='{"whatsapp":2,"call":0.333,"facebook":120}',
    )
    assert s.sla_ack_minutes_by_channel_json == '{"whatsapp":2,"call":0.333,"facebook":120}'
