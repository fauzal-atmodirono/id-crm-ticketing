from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

from chatbot.features.chat.adapters.bigquery_metrics import (
    BigQueryMetricsAdapter,
    NoOpMetrics,
    build_metrics_port,
)
from chatbot.features.metrics.events import build_turn_event
from chatbot.platform.config import Settings


class _FakeBQ:
    """Captures table/view/insert calls instead of hitting BigQuery."""

    def __init__(self) -> None:
        self.created_datasets: list[str] = []
        self.created_tables: list[str] = []
        self.queries: list[str] = []
        self.inserted: list[tuple[str, list[dict[str, Any]]]] = []
        self.insert_error: Exception | None = None

    def create_dataset(self, ref: str, exists_ok: bool = False) -> None:
        self.created_datasets.append(ref)

    def create_table(self, table: Any, exists_ok: bool = False) -> None:
        self.created_tables.append(str(table))

    def query(self, ddl: str) -> Any:
        self.queries.append(ddl)
        return SimpleNamespace(result=lambda: None)

    def insert_rows_json(self, table_id: str, rows: list[dict[str, Any]]) -> list[Any]:
        if self.insert_error is not None:
            raise self.insert_error
        self.inserted.append((table_id, rows))
        return []


def _settings() -> Settings:
    return cast(
        Settings,
        SimpleNamespace(
            bigquery_project_id="proj",
            bigquery_dataset="ds",
            bigquery_turn_events_table="turn_events",
            metrics_provider="bigquery",
        ),
    )


async def test_noop_emit_turn_is_a_noop() -> None:
    ev = build_turn_event("sim-1", datetime.now(UTC), 100, 1, False, False)
    # Must not raise and must return None.
    assert await NoOpMetrics().emit_turn(ev) is None


def test_init_ensures_dataset_table_and_views() -> None:
    fake = _FakeBQ()
    BigQueryMetricsAdapter(_settings(), client=fake)
    assert fake.created_datasets == ["proj.ds"]
    assert fake.created_tables == ["proj.ds.turn_events"]
    assert len(fake.queries) == 3  # the three views


async def test_emit_turn_inserts_one_row_with_event_id() -> None:
    fake = _FakeBQ()
    adapter = BigQueryMetricsAdapter(_settings(), client=fake)
    ev = build_turn_event("whatsapp-+60", datetime(2026, 6, 29, tzinfo=UTC), 1500, 1, True, False)
    await adapter.emit_turn(ev)
    assert len(fake.inserted) == 1
    table_id, rows = fake.inserted[0]
    assert table_id == "proj.ds.turn_events"
    row = rows[0]
    assert row["event_id"]  # uuid minted at emit
    assert row["channel"] == "WhatsApp"
    assert row["session_id"] == "whatsapp-+60"
    assert row["latency_ms"] == 1500
    assert row["is_first_turn"] is True
    assert row["is_fallback"] is True
    assert row["handed_off"] is False
    assert row["occurred_at"].startswith("2026-06-29T")


async def test_emit_turn_swallows_insert_errors() -> None:
    fake = _FakeBQ()
    fake.insert_error = RuntimeError("BQ down")
    adapter = BigQueryMetricsAdapter(_settings(), client=fake)
    ev = build_turn_event("sim-1", datetime.now(UTC), 100, 1, False, False)
    # Must not raise.
    assert await adapter.emit_turn(ev) is None


def test_build_metrics_port_selects_noop_by_default() -> None:
    s = cast(Settings, SimpleNamespace(metrics_provider="noop"))
    assert build_metrics_port(s).__class__.__name__ == "NoOpMetrics"
