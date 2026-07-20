import os
import pytest
from chatbot.platform.config import Settings


def test_assist_settings_defaults() -> None:
    s = Settings()
    assert s.proton_backend_key == ""
    assert s.assist_gemini_model == "gemini-2.5-flash"
    assert isinstance(s.assist_cors_origins, list)


def test_assist_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROTON_BACKEND_KEY", "secret123")
    monkeypatch.setenv("ASSIST_GEMINI_MODEL", "gemini-2.5-pro")
    monkeypatch.setenv("ASSIST_CORS_ORIGINS", '["http://crm.example.com"]')
    s = Settings()
    assert s.proton_backend_key == "secret123"
    assert s.assist_gemini_model == "gemini-2.5-pro"
    assert s.assist_cors_origins == ["http://crm.example.com"]
