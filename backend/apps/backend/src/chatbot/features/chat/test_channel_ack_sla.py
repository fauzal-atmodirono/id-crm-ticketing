"""Tests for per-channel first-response (ack) SLA config (Task 6 + Task 7).

Settings() is constructed with no overrides — all fields have defaults and
the suite runs with GOOGLE_API_KEY=dummy in the environment, which is the
only env var the google-genai SDK requires at import time. This mirrors the
pattern used in test_chatwoot_config.py and test_routing_config.py.

Task 7 tests extend this file with:
- Unit tests for _parse_ack_minutes and _conversation_channel helpers.
- A behavioral test for per-channel ack threshold in scan_conversations,
  mirroring the fixtures in test_sla.py exactly.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from chatbot.features.chat import sla
from chatbot.features.chat.adapters.audit_log import InMemoryAuditLog
from chatbot.features.chat.sla import NO_RESPONSE_BREACH, scan_conversations
from chatbot.platform.config import Settings

_NOW = datetime(2026, 7, 17, 12, 0, 0, tzinfo=UTC)


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "sla_response_hours": 8,
        "sla_resolution_hours": 48,
        "sla_scan_interval_minutes": 15,
        "chatwoot_inbox_id": 1,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


def _epoch_minutes_ago(minutes: float, now: datetime = _NOW) -> int:
    return int((now - timedelta(minutes=minutes)).timestamp())


# ---------------------------------------------------------------------------
# Task 6: Settings field tests
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Task 7: helper unit tests
# ---------------------------------------------------------------------------


def test_parse_ack_minutes_valid_and_invalid() -> None:
    assert sla._parse_ack_minutes('{"whatsapp": 2, "email": 240}') == {
        "whatsapp": 2.0,
        "email": 240.0,
    }
    assert sla._parse_ack_minutes("") == {}
    assert sla._parse_ack_minutes("not json") == {}


def test_conversation_channel_normalization() -> None:
    assert sla._conversation_channel({"meta": {"channel": "Channel::Whatsapp"}}) == "whatsapp"
    assert sla._conversation_channel({"meta": {"channel": "Channel::Email"}}) == "email"
    assert sla._conversation_channel({"meta": {"channel": "Channel::FacebookPage"}}) == "facebook"
    assert sla._conversation_channel({"meta": {"channel": "Channel::Instagram"}}) == "instagram"
    assert sla._conversation_channel({}) is None


# ---------------------------------------------------------------------------
# Task 7: behavioral test — per-channel ack threshold in scan_conversations
# ---------------------------------------------------------------------------


def test_whatsapp_acks_breach_at_2_minutes_while_global_is_hours() -> None:
    """WhatsApp conv (no first_reply) breaches NO_RESPONSE_BREACH at 3 min when
    the per-channel override is 2 min, while global sla_response_hours=8h.
    A control conv on a channel with no override does NOT breach at 3 min.
    """
    audit = InMemoryAuditLog()
    settings = _settings(
        sla_response_hours=8,  # 8h global — would NOT fire at 3 min
        sla_resolution_hours=1000,
        sla_ack_minutes_by_channel_json='{"whatsapp":2}',
    )
    # 3 minutes old, no first_reply_created_at
    conv_wa: dict[str, Any] = {
        "id": 1,
        "status": "open",
        "created_at": _epoch_minutes_ago(3),
        "meta": {"channel": "Channel::Whatsapp"},
    }

    fired = asyncio.run(
        scan_conversations(settings, audit, now=_NOW, fetch=lambda _s: [conv_wa])
    )
    assert any(e.to_state == NO_RESPONSE_BREACH for e in fired), (
        "Expected NO_RESPONSE_BREACH for WhatsApp conv at 3 min with 2-min override"
    )

    # Control: same age, but Channel::Email has no override → uses 8h global → no breach
    audit2 = InMemoryAuditLog()
    conv_email: dict[str, Any] = {
        "id": 2,
        "status": "open",
        "created_at": _epoch_minutes_ago(3),
        "meta": {"channel": "Channel::Email"},
    }
    fired2 = asyncio.run(
        scan_conversations(settings, audit2, now=_NOW, fetch=lambda _s: [conv_email])
    )
    assert not any(e.to_state == NO_RESPONSE_BREACH for e in fired2), (
        "Expected NO breach for Email conv at 3 min with no override (global 8h applies)"
    )
