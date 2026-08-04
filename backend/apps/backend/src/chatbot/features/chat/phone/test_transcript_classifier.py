from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from structlog.testing import capture_logs

from chatbot.features.chat.phone.transcript_classifier import classify


def _gemini(text: str | None = None, raises: Exception | None = None) -> MagicMock:
    genai = MagicMock()
    if raises is not None:
        genai.aio.models.generate_content = AsyncMock(side_effect=raises)
    else:
        response = MagicMock()
        response.text = text
        genai.aio.models.generate_content = AsyncMock(return_value=response)
    return genai


async def test_well_formed_response_maps_all_four_keys() -> None:
    gemini = _gemini(
        text=(
            '{"case_type": "Complaint", "division": "Aftersales", '
            '"concern": "Service Operation", "status": "open"}'
        )
    )
    result = await classify("USER: my car needs service\nASSISTANT: sorry to hear", gemini)
    assert result == {
        "case_type": "Complaint",
        "division": "Aftersales",
        "concern": "Service Operation",
        "status": "open",
    }


async def test_unparseable_response_returns_empty_dict() -> None:
    gemini = _gemini(text="not json at all")
    result = await classify("USER: hi", gemini)
    assert result == {}


async def test_raised_exception_returns_empty_dict() -> None:
    gemini = _gemini(raises=RuntimeError("gemini down"))
    result = await classify("USER: hi", gemini)
    assert result == {}


async def test_invalid_division_dropped_but_other_valid_keys_kept() -> None:
    """The important test: an invented/mistyped division must not be written
    through (it would silently corrupt Package E's reporting buckets), but a
    correctly classified case_type/status alongside it must still survive."""
    gemini = _gemini(text='{"case_type": "Inquiry", "division": "Legal", "status": "resolved"}')
    with capture_logs() as captured:
        result = await classify("USER: hi", gemini)
    assert "division" not in result
    assert result["case_type"] == "Inquiry"
    assert result["status"] == "resolved"
    assert any(e["event"] == "phone_transcript_classify_invalid_division" for e in captured)


async def test_invalid_case_type_dropped() -> None:
    gemini = _gemini(text='{"case_type": "Question", "division": "Sales"}')
    result = await classify("USER: hi", gemini)
    assert "case_type" not in result
    assert result["division"] == "Sales"


async def test_invalid_status_dropped() -> None:
    gemini = _gemini(text='{"status": "escalated", "division": "Sales"}')
    result = await classify("USER: hi", gemini)
    assert "status" not in result
    assert result["division"] == "Sales"


async def test_division_matched_case_insensitively_to_canonical_spelling() -> None:
    gemini = _gemini(text='{"division": "sales"}')
    result = await classify("USER: hi", gemini)
    assert result["division"] == "Sales"


async def test_non_dict_json_response_returns_empty_dict() -> None:
    gemini = _gemini(text="[1, 2, 3]")
    result = await classify("USER: hi", gemini)
    assert result == {}


async def test_empty_transcript_returns_empty_dict_without_calling_gemini() -> None:
    gemini = _gemini(text='{"case_type": "Inquiry"}')
    result = await classify("   ", gemini)
    assert result == {}
    gemini.aio.models.generate_content.assert_not_called()


async def test_blank_concern_is_dropped() -> None:
    gemini = _gemini(text='{"concern": "   "}')
    result = await classify("USER: hi", gemini)
    assert "concern" not in result


async def test_concern_is_length_capped() -> None:
    gemini = _gemini(text='{"concern": "' + ("x" * 500) + '"}')
    result = await classify("USER: hi", gemini)
    assert len(result["concern"]) == 200
