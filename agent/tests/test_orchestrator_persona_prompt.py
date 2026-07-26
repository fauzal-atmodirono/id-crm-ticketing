# test_orchestrator_persona_prompt.py
from app.services.orchestrator import SYSTEM_PROMPT, _build_system_prompt


def test_none_persona_returns_verbatim() -> None:
    assert _build_system_prompt(None) == SYSTEM_PROMPT


def test_empty_persona_returns_verbatim() -> None:
    assert _build_system_prompt({"instructions": "", "guardrails": [], "language": ""}) == SYSTEM_PROMPT


def test_instructions_override_base() -> None:
    out = _build_system_prompt({"instructions": "You are Ana.", "guardrails": [], "language": ""})
    assert out.startswith("You are Ana.")
    assert SYSTEM_PROMPT not in out


def test_guardrails_and_language_appended() -> None:
    out = _build_system_prompt({"instructions": "", "guardrails": ["No prices"], "language": "Bahasa Melayu"})
    assert out.startswith(SYSTEM_PROMPT)  # default base kept
    assert "## Guardrails" in out and "- No prices" in out
    assert "Always reply in Bahasa Melayu." in out
