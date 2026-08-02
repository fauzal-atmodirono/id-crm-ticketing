from chatbot.features.assist.router import _build_persona_prefix


def test_language_appended_as_default_not_override() -> None:
    out = _build_persona_prefix("", [], "Bahasa Melayu")
    assert (
        "Default to Bahasa Melayu when no language is otherwise indicated, "
        "but always follow this task's own language instructions below "
        "if they say otherwise." in out
    )


def test_no_language_line_when_empty() -> None:
    out = _build_persona_prefix("", [], "")
    assert "Default to" not in out


def test_product_and_guardrails_and_language_all_present() -> None:
    out = _build_persona_prefix("Proton X50", ["No prices"], "Bahasa Melayu")
    assert "Product: Proton X50" in out
    assert "## Guardrails" in out and "- No prices" in out
    assert "Default to Bahasa Melayu" in out


def test_all_absent_returns_empty_string() -> None:
    assert _build_persona_prefix("", [], "") == ""
