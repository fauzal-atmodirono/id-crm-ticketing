"""QaLabelPort BigQuery adapter (Phase 4) + the port factory."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import structlog
from google.cloud import bigquery

from chatbot.features.metrics.qa import NoOpQaLabels, QaLabel
from chatbot.features.metrics.qa_schema import QA_LABELS_SCHEMA, qa_view_ddls

if TYPE_CHECKING:
    from chatbot.features.metrics.qa import QaLabelPort
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


class BigQueryQaLabels:
    """Writes one row per manual QA label into BigQuery. Ensures the table +
    v_quality view on init; record is best-effort, non-blocking, and never raises."""

    def __init__(self, settings: Settings, *, client: Any | None = None) -> None:
        self._project = settings.bigquery_project_id
        self._dataset = settings.bigquery_dataset
        self._table = settings.bigquery_qa_labels_table
        self._table_id = f"{self._project}.{self._dataset}.{self._table}"
        self._client = client or bigquery.Client(project=self._project)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        try:
            self._client.create_dataset(f"{self._project}.{self._dataset}", exists_ok=True)
            self._client.create_table(
                bigquery.Table(self._table_id, schema=QA_LABELS_SCHEMA), exists_ok=True
            )
            for ddl in qa_view_ddls(self._project, self._dataset, self._table).values():
                self._client.query(ddl).result()
        except Exception as e:  # best-effort bootstrap (v_quality needs conversations to exist)
            _log.error("qa_ensure_schema_failed", error=str(e))

    async def record_label(self, label: QaLabel) -> None:
        try:
            row = {
                "conversation_id": label.conversation_id,
                "accuracy": label.accuracy,
                "quality": label.quality,
                "reviewer": label.reviewer,
                "notes": label.notes,
                "labeled_at": label.labeled_at.isoformat(),
            }
            errors = await asyncio.to_thread(self._client.insert_rows_json, self._table_id, [row])
            if errors:
                _log.warning("qa_insert_returned_errors", errors=str(errors))
        except Exception as e:  # best-effort: never break the response
            _log.error("qa_record_label_failed", error=str(e))


def build_qa_label_port(settings: Settings) -> QaLabelPort:
    """Pick the QaLabelPort implementation from settings."""
    if settings.qa_provider == "bigquery":
        try:
            return BigQueryQaLabels(settings)
        except Exception as e:  # never let QA init crash the app
            _log.error("qa_adapter_init_failed_falling_back_to_noop", error=str(e))
            return NoOpQaLabels()
    return NoOpQaLabels()
