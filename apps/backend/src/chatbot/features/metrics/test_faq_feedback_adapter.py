from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

import pytest

from chatbot.features.metrics.faq_feedback import FaqFeedback
from chatbot.features.metrics.faq_feedback_adapter import (
    BigQueryFaqFeedback,
    build_faq_feedback_port,
)
from chatbot.platform.config import Settings


def _settings_stub() -> Settings:
    return cast(
        Settings,
        SimpleNamespace(
            bigquery_project_id="proj",
            bigquery_dataset="ds",
            bigquery_faq_feedback_table="faq_feedback",
            metrics_provider="bigquery",
        ),
    )


@pytest.mark.asyncio
async def test_bq_adapter_inserts_row() -> None:
    inserted: list[dict[str, object]] = []

    class _Client:
        def insert_rows_json(self, table: str, rows: list[dict[str, object]]) -> list[object]:
            inserted.extend(rows)
            return []  # empty = no errors

    settings = _settings_stub()
    adapter = BigQueryFaqFeedback(settings, client=_Client())
    await adapter.record_feedback(
        FaqFeedback("A1", "sim-1", helpful=True, score=4, at=datetime.now(UTC))
    )
    assert inserted[0]["article_id"] == "A1" and inserted[0]["helpful"] is True


def test_build_faq_feedback_port_selects_noop_by_default() -> None:
    s = cast(Settings, SimpleNamespace(metrics_provider="noop"))
    assert build_faq_feedback_port(s).__class__.__name__ == "NoOpFaqFeedback"


async def test_record_feedback_swallows_errors() -> None:
    class _FailingClient:
        def insert_rows_json(self, table: str, rows: list[dict[str, object]]) -> list[object]:
            raise RuntimeError("BQ down")

    settings = _settings_stub()
    adapter = BigQueryFaqFeedback(settings, client=_FailingClient())
    # Must not raise.
    await adapter.record_feedback(
        FaqFeedback("A1", "sim-1", helpful=True, score=4, at=datetime.now(UTC))
    )
