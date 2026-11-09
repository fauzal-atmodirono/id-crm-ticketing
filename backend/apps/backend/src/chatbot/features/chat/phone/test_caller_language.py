"""Start the call in the caller's language; never drift (2026-11-09)."""

from __future__ import annotations

from chatbot.features.chat.phone.caller_context import (
    CallerContext,
    _candidate,
    build_language_instruction,
    language_name,
)
from chatbot.features.demo.personas import persona_language


def test_language_names() -> None:
    assert language_name("en") == "English"
    assert language_name("ms-MY") == "Bahasa Melayu"
    assert language_name("zh") == "Chinese (Mandarin)"
    assert language_name("English") == "English"
    assert language_name("") == "" and language_name("hi") == ""


def test_instruction_starts_in_the_known_language_and_never_drifts() -> None:
    text = build_language_instruction("ms")
    assert "start the call in Bahasa Melayu" in text
    assert "Switch ONLY when the caller themselves clearly says a full sentence" in text
    assert "brand name" in text and "knowledge-base result" in text
    assert "background noise" in text
    assert "closing rating question" in text
    assert "switch immediately" not in text


def test_instruction_for_an_unknown_language_greets_in_english() -> None:
    assert "greet in English" in build_language_instruction("")
    assert "greet in English" in build_language_instruction("hi")


def test_start_language_prefers_the_call_level_language() -> None:
    ctx = CallerContext.from_candidates(
        "+60000000101", "web_call_as", [{"contact_id": 1, "name": "A", "language": "zh"}]
    )
    assert ctx.start_language == "zh"
    ctx.language = "en"
    assert ctx.start_language == "en"


def test_contact_stored_language_is_read() -> None:
    c = _candidate({"id": 5, "name": "Nurul", "custom_attributes": {"preferred_language": "ms"}}, [])
    assert c.language == "ms"
    assert _candidate({"id": 6, "name": "X"}, []).language == ""


def test_persona_language_by_number() -> None:
    personas = [{"number": "+60000000101", "language": "en"}, {"number": "+60000000102"}]
    assert persona_language("+60000000101", personas) == "en"
    assert persona_language("+60000000102", personas) == ""
    assert persona_language("+60000000999", personas) == ""
    assert persona_language(None, personas) == ""
