# test_orchestrator_persona_prompt.py
from app.services.orchestrator import LANGUAGE_MATCH_INSTRUCTION, SYSTEM_PROMPT, _build_system_prompt


def test_none_persona_returns_verbatim() -> None:
    assert _build_system_prompt(None) == SYSTEM_PROMPT


def test_empty_persona_returns_verbatim() -> None:
    assert _build_system_prompt({"instructions": "", "guardrails": [], "language": ""}) == SYSTEM_PROMPT


def test_instructions_override_base_but_keep_language_match() -> None:
    out = _build_system_prompt({"instructions": "You are Ana.", "guardrails": [], "language": ""})
    assert out.startswith("You are Ana.")
    assert SYSTEM_PROMPT not in out
    assert LANGUAGE_MATCH_INSTRUCTION in out


def test_guardrails_and_language_appended() -> None:
    out = _build_system_prompt({"instructions": "", "guardrails": ["No prices"], "language": "Bahasa Melayu"})
    assert out.startswith(SYSTEM_PROMPT)  # default base kept, already has LANGUAGE_MATCH_INSTRUCTION
    assert "## Guardrails" in out and "- No prices" in out
    assert (
        "Prefer Bahasa Melayu when the customer's language is unclear, but "
        "always match the language the customer writes in." in out
    )


def test_instructions_and_language_both_set_keep_language_match() -> None:
    out = _build_system_prompt(
        {"instructions": "You are Ana.", "guardrails": [], "language": "Bahasa Melayu"}
    )
    assert out.startswith("You are Ana.")
    assert LANGUAGE_MATCH_INSTRUCTION in out
    assert "Prefer Bahasa Melayu when the customer's language is unclear" in out
