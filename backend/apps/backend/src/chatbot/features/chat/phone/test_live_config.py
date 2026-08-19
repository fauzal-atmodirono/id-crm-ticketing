from __future__ import annotations

from chatbot.features.chat.phone.gemini_live import _build_live_config
from chatbot.platform.config import get_settings


def test_language_code_set_when_configured() -> None:
    s = get_settings()
    s.gemini_live_voice = "Kore"
    s.gemini_live_language = "ms-MY"
    cfg = _build_live_config(s, "sys instruction", [])
    sc = cfg.speech_config
    assert sc is not None
    assert sc.language_code == "ms-MY"
    vc = sc.voice_config
    assert vc is not None and vc.prebuilt_voice_config is not None
    assert vc.prebuilt_voice_config.voice_name == "Kore"


def test_language_code_omitted_by_default() -> None:
    s = get_settings()
    s.gemini_live_language = ""
    cfg = _build_live_config(s, "sys instruction", [])
    # Unset language must not be forced — native-audio auto-detects.
    assert cfg.speech_config is not None
    assert not cfg.speech_config.language_code


def _s(**over):
    from chatbot.platform.config import get_settings

    return get_settings().model_copy(update=over)


def test_vad_defaults_to_low_sensitivity_for_telephony():
    """Reported from proton: 'a little sound will be breaking the AI agent'.
    Gemini's DEFAULT VAD assumes a clean mic; on mu-law 8 kHz phone audio line
    noise crossed the threshold and barged in, cutting the assistant off."""
    from google.genai import types

    from chatbot.features.chat.phone.gemini_live import _build_live_config

    cfg = _build_live_config(_s(), "sys", [])
    vad = cfg.realtime_input_config.automatic_activity_detection
    assert vad.start_of_speech_sensitivity == types.StartSensitivity.START_SENSITIVITY_LOW
    assert vad.end_of_speech_sensitivity == types.EndSensitivity.END_SENSITIVITY_LOW
    assert vad.silence_duration_ms == 900
    assert vad.prefix_padding_ms == 300


def test_vad_can_be_disabled_back_to_sdk_defaults():
    from chatbot.features.chat.phone.gemini_live import _build_live_config

    cfg = _build_live_config(_s(gemini_live_vad_enabled=False), "sys", [])
    assert cfg.realtime_input_config is None


def test_vad_sensitivity_is_overridable():
    from google.genai import types

    from chatbot.features.chat.phone.gemini_live import _build_live_config

    cfg = _build_live_config(_s(gemini_live_vad_start_sensitivity="HIGH"), "sys", [])
    vad = cfg.realtime_input_config.automatic_activity_detection
    assert vad.start_of_speech_sensitivity == types.StartSensitivity.START_SENSITIVITY_HIGH


def test_unknown_sensitivity_falls_back_to_low_rather_than_raising():
    """This runs while a caller is connecting: a typo in a tenant env must not
    fail the call, and LOW is the safer wrong answer -- it under-triggers
    rather than interrupting the caller."""
    from google.genai import types

    from chatbot.features.chat.phone.gemini_live import _build_live_config

    cfg = _build_live_config(_s(gemini_live_vad_start_sensitivity="banana"), "sys", [])
    vad = cfg.realtime_input_config.automatic_activity_detection
    assert vad.start_of_speech_sensitivity == types.StartSensitivity.START_SENSITIVITY_LOW
