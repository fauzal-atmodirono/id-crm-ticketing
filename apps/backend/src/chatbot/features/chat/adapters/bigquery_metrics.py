"""MetricsPort adapters: NoOp (default) and BigQuery streaming (Phase 2)."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import structlog
from google.cloud import bigquery

from chatbot.features.metrics.events import TurnEvent
from chatbot.features.metrics.turn_schema import TURN_EVENTS_SCHEMA, turn_view_ddls

if TYPE_CHECKING:
    from chatbot.features.chat.ports import MetricsPort
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


class NoOpMetrics:
    """Default MetricsPort — drops every event. Used in dev/tests and when
    metrics_provider != 'bigquery'."""

    async def emit_turn(self, _event: TurnEvent) -> None:
        return None


class BigQueryMetricsAdapter:
    """Streams one row per turn into BigQuery. Ensures the table + views exist on
    init; emit is best-effort and never raises."""

    def __init__(self, settings: Settings, *, client: Any | None = None) -> None:
        self._project = settings.bigquery_project_id
        self._dataset = settings.bigquery_dataset
        self._table = settings.bigquery_turn_events_table
        self._table_id = f"{self._project}.{self._dataset}.{self._table}"
        self._client = client or bigquery.Client(project=self._project)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        try:
            self._client.create_dataset(f"{self._project}.{self._dataset}", exists_ok=True)
            self._client.create_table(
                bigquery.Table(self._table_id, schema=TURN_EVENTS_SCHEMA), exists_ok=True
            )
            for ddl in turn_view_ddls(self._project, self._dataset, self._table).values():
                self._client.query(ddl).result()
        except Exception as e:  # best-effort bootstrap
            _log.error("metrics_ensure_schema_failed", error=str(e))

    async def emit_turn(self, event: TurnEvent) -> None:
        try:
            row = {
                "event_id": uuid.uuid4().hex,
                "occurred_at": event.occurred_at.isoformat(),
                "channel": event.channel,
                "session_id": event.session_id,
                "latency_ms": event.latency_ms,
                "is_first_turn": event.is_first_turn,
                "is_fallback": event.is_fallback,
                "handed_off": event.handed_off,
            }
            errors = self._client.insert_rows_json(self._table_id, [row])
            if errors:
                _log.warning("metrics_insert_returned_errors", errors=str(errors))
        except Exception as e:  # best-effort: never break the turn
            _log.error("metrics_emit_turn_failed", error=str(e))


def build_metrics_port(settings: Settings) -> MetricsPort:
    """Pick the MetricsPort implementation from settings."""
    if settings.metrics_provider == "bigquery":
        return BigQueryMetricsAdapter(settings)
    return NoOpMetrics()
