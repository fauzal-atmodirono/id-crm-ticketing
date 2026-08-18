import pytest

from chatbot.platform.config import Settings, get_settings


def test_phone_settings_have_defaults() -> None:
    s = get_settings()
    assert s.gemini_live_model  # non-empty default
    assert s.gemini_live_voice
    # secrets default to empty (set via env in deployment)
    assert hasattr(s, "twilio_api_key_sid")
    assert hasattr(s, "twilio_api_key_secret")
    assert hasattr(s, "twilio_twiml_app_sid")
    assert hasattr(s, "public_wss_base_url")


def test_agent_softphone_defaults_off() -> None:
    """The whole feature must be inert until a tenant opts in."""
    s = get_settings()
    assert s.phone_agent_softphone_enabled is False
    assert s.phone_agent_token_ttl_seconds == 300
    assert s.phone_softphone_registration_ttl_seconds == 90
    assert s.phone_agent_ring_timeout_seconds == 20
    assert s.phone_fanout_ring_timeout_seconds == 25
    assert s.phone_fanout_max_agents == 10


def test_agent_softphone_requires_handoff_enabled() -> None:
    """Stage 1's <Dial action> is /webhooks/phone/dial-status, which
    router.py only registers when phone_handoff_enabled is on. Without it
    Twilio would POST the dial outcome to a 404 and drop a live caller."""
    with pytest.raises(ValueError, match="PHONE_AGENT_SOFTPHONE_ENABLED requires"):
        Settings(
            phone_agent_softphone_enabled=True,
            phone_handoff_enabled=False,
            phone_transcript_live_enabled=True,
        )
