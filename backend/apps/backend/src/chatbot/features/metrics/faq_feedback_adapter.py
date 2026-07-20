"""BigQuery FAQ-feedback adapter + factory."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog
from google.cloud import bigquery

from chatbot.features.metrics.faq_feedback import FaqFeedback, FaqFeedbackPort, NoOpFaqFeedback

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


class BigQueryFaqFeedback:
    def __init__(self, settings: Settings, *, client: Any | None = None) -> None:
        self._table = (
            f"{settings.bigquery_project_id}.{settings.bigquery_dataset}."
            f"{settings.bigquery_faq_feedback_table}"
        )
        self._client = client or bigquery.Client(project=settings.bigquery_project_id)

    async def record_feedback(self, fb: FaqFeedback) -> None:
        try:
            errors = self._client.insert_rows_json(
                self._table,
                [
                    {
                        "article_id": fb.article_id,
                        "session_id": fb.session_id,
                        "helpful": fb.helpful,
                        "score": fb.score,
                        "at": fb.at.isoformat(),
                    }
                ],
            )
            if errors:
                _log.error("faq_feedback_insert_errors", errors=errors)
        except Exception as e:  # best-effort: feedback must never break the request
            _log.error("faq_feedback_insert_failed", error=str(e))


def build_faq_feedback_port(settings: Settings) -> FaqFeedbackPort:
    if settings.metrics_provider == "bigquery":
        try:
            return BigQueryFaqFeedback(settings)
        except Exception as e:
            _log.error("faq_feedback_init_failed_falling_back_to_noop", error=str(e))
    return NoOpFaqFeedback()
