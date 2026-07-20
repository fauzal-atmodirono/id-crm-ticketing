from __future__ import annotations

from chatbot.platform.config import Settings


def test_twilio_settings_default_to_empty() -> None:
    settings = Settings(_env_file=None)
    assert settings.twilio_account_sid == ""
    assert settings.twilio_auth_token == ""
    assert settings.twilio_whatsapp_number == ""
    assert settings.twilio_phone_number == ""
    assert settings.twilio_webhook_base_url == ""


def test_twilio_settings_read_from_init() -> None:
    settings = Settings(
        _env_file=None,
        twilio_account_sid="AC123",
        twilio_auth_token="tok",
        twilio_whatsapp_number="whatsapp:+60123456789",
    )
    assert settings.twilio_account_sid == "AC123"
    assert settings.twilio_auth_token == "tok"
    assert settings.twilio_whatsapp_number == "whatsapp:+60123456789"
