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
