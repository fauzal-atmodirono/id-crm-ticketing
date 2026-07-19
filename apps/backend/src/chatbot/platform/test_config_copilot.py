import pytest
from chatbot.platform.config import Settings


def test_copilot_settings_defaults() -> None:
    s = Settings()
    assert s.copilot_gemini_model == "gemini-2.5-flash"
    assert s.copilot_max_tool_iterations == 5


def test_copilot_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COPILOT_GEMINI_MODEL", "gemini-2.5-pro")
    monkeypatch.setenv("COPILOT_MAX_TOOL_ITERATIONS", "8")
    s = Settings()
    assert s.copilot_gemini_model == "gemini-2.5-pro"
    assert s.copilot_max_tool_iterations == 8
