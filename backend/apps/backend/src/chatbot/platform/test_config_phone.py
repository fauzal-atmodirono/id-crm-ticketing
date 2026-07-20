from chatbot.platform.config import get_settings


def test_phone_settings_have_defaults() -> None:
    s = get_settings()
    assert s.gemini_live_model  # non-empty default
    assert s.gemini_live_voice
    # secrets default to empty (set via env in deployment)
    assert hasattr(s, "twilio_api_key_sid")
    assert hasattr(s, "twilio_api_key_secret")
    assert hasattr(s, "twilio_twiml_app_sid")
    assert hasattr(s, "public_wss_base_url")
