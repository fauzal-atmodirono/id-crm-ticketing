"""MetricsPort adapters: NoOp (default) and BigQuery streaming (Phase 2)."""

from __future__ import annotations

from chatbot.features.metrics.events import TurnEvent


class NoOpMetrics:
    """Default MetricsPort — drops every event. Used in dev/tests and when
    metrics_provider != 'bigquery'."""

    async def emit_turn(self, _event: TurnEvent) -> None:
        return None
