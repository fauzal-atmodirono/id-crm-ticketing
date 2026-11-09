"""Customer context reaches the decision prompt, and its absence changes
nothing. The second half matters more than the first: every other tenant runs
this code path with contacts that carry no nasabah attributes at all."""

from __future__ import annotations

from app.services.orchestrator import SYSTEM_PROMPT, _build_system_prompt

PERSONA = {
    "instructions": "You are Bahana's relationship assistant.",
    "guardrails": ["Never promise a return."],
    "language": "Bahasa Indonesia",
}

CONTEXT = "## Customer profile (from the CRM record for this contact)\n- Risk profile: Moderat"


def test_no_persona_no_context_is_byte_identical_to_today():
    assert _build_system_prompt(None) == SYSTEM_PROMPT
    assert _build_system_prompt(None, "") == SYSTEM_PROMPT


def test_persona_without_context_is_unchanged():
    assert _build_system_prompt(PERSONA, "") == _build_system_prompt(PERSONA)


def test_empty_persona_dict_without_context_is_still_the_default():
    assert _build_system_prompt({}, "") == SYSTEM_PROMPT
    assert _build_system_prompt({"instructions": "", "guardrails": [], "language": ""}, "") == SYSTEM_PROMPT


def test_context_is_appended_when_there_is_no_persona():
    out = _build_system_prompt(None, CONTEXT)
    assert out.startswith(SYSTEM_PROMPT)
    assert CONTEXT in out


def test_context_is_appended_after_the_persona():
    out = _build_system_prompt(PERSONA, CONTEXT)
    assert "Bahana's relationship assistant" in out
    assert "Never promise a return." in out
    assert out.rstrip().endswith(CONTEXT)


def test_context_never_replaces_the_persona_guardrails():
    out = _build_system_prompt(PERSONA, CONTEXT)
    assert "Guardrails" in out
