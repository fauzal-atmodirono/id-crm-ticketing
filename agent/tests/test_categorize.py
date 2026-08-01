from unittest.mock import AsyncMock

import pytest

from app.services import categorize


async def test_classify_returns_matching_slug(monkeypatch):
    monkeypatch.setattr(categorize.gemini, "generate", AsyncMock(return_value="  battery  "))
    result = await categorize.classify_category("car won't charge", ["battery", "sales"])
    assert result == "battery"


async def test_classify_rejects_non_candidate(monkeypatch):
    monkeypatch.setattr(categorize.gemini, "generate", AsyncMock(return_value="weather"))
    result = await categorize.classify_category("hello", ["battery", "sales"])
    assert result is None


async def test_classify_failopen_on_error(monkeypatch):
    monkeypatch.setattr(categorize.gemini, "generate", AsyncMock(side_effect=RuntimeError("boom")))
    result = await categorize.classify_category("hello", ["battery"])
    assert result is None


async def test_classify_empty_candidates_is_none():
    assert await categorize.classify_category("hello", []) is None


def test_candidate_slugs_returns_taxonomy_main_categories():
    from app.services.case_taxonomy import build_case_taxonomy
    from app.config import Settings

    s = Settings(
        case_taxonomy_json='{"battery": {"label": "Battery"}, "sales": {"label": "Sales"}}'
    )
    taxonomy = build_case_taxonomy(s)
    assert categorize._candidate_slugs(taxonomy) == ["battery", "sales"]
